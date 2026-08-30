"""DEC-05 tool-fault injection callbacks (SPEC §17.2).

Tool faults may target ONLY mutation tools. A tool is a mutation target when
its name matches ``reserve_*`` / ``request_*`` / ``replace_*`` or when the
scenario author explicitly names it in ``faults[].tool``. Reads
(``get_*`` / ``check_*`` / ``verify_*``) are REFUSED as fault targets because
faulting a truthful read corrupts the evaluation baseline (DEC-05).

Fault state is PER-RUN. :class:`ScenarioEngine.run` enters :func:`run_context`,
which installs a fresh :class:`FaultContext` in a contextvar, so two concurrent
engine runs never share armed or applied fault state. The contextvar propagates
to every task the run spawns (fault schedulers, the agent under test), so the
callbacks below read the current run's state on every tool invocation.

Outside a run, the module-level containers below remain the fault state. During
a run they are rebound to that run's containers, so observers outside the run's
task tree (e.g. the chaos harness's fault-window healers, which read
``_active_faults`` / call :func:`snapshot_applied_faults` from their own tasks)
keep seeing the active run's state.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Any

from agentlab.backend.scenarios.models import ScenarioConfigError, ScenarioFault

_READ_TOOL_PREFIXES = ("get_", "check_", "verify_")
_MUTATION_TOOL_PREFIXES = ("reserve_", "request_", "replace_")


class FaultContext:
    """Per-run fault state: the armed-fault registry plus the applied record."""

    def __init__(
        self,
        active: dict[str, str] | None = None,
        applied: list[dict[str, Any]] | None = None,
    ) -> None:
        self.active: dict[str, str] = active if active is not None else {}
        self.applied: list[dict[str, Any]] = applied if applied is not None else []


# Module-level fault state, used whenever no per-run context is installed. The
# engine arms faults on schedule; the callbacks below read the current context
# on every tool invocation. During a run these names are rebound to the run's
# containers; the originals below are restored when no run is active.
_active_faults: dict[str, str] = {}
_applied_faults: list[dict[str, Any]] = []
_original_active = _active_faults
_original_applied = _applied_faults
_active_run_contexts: list[FaultContext] = []

_current_context: ContextVar[FaultContext | None] = ContextVar(
    "agentlab_fault_context", default=None
)


def _context() -> FaultContext:
    """Return the current run's fault context, or the module-level state."""
    ctx = _current_context.get()
    if ctx is None:
        ctx = FaultContext(_active_faults, _applied_faults)
    return ctx


@contextlib.contextmanager
def run_context() -> Iterator[FaultContext]:
    """Install a fresh per-run fault context for one scenario run.

    Concurrent runs are isolated through the contextvar; the module-level
    containers are rebound to the most recently started still-active run so
    module-level observers outside the run's task tree see an active run, and
    restored to the originals once no run is active.
    """
    global _active_faults, _applied_faults
    ctx = FaultContext()
    token = _current_context.set(ctx)
    _active_run_contexts.append(ctx)
    _active_faults, _applied_faults = ctx.active, ctx.applied
    try:
        yield ctx
    finally:
        _current_context.reset(token)
        _active_run_contexts.remove(ctx)
        if _active_run_contexts:
            inner = _active_run_contexts[-1]
            _active_faults, _applied_faults = inner.active, inner.applied
        else:
            _active_faults, _applied_faults = _original_active, _original_applied

# Fake SUCCESS shapes for ``success_without_state_change``, keyed by tool name.
# Shapes mirror the real device-tool return shapes (SPEC §10): reserve_device
# returns {"reserved": true, "device": {...}}; request_replacement returns
# {"order": {...}}. Callables receive the tool arguments and interpolate them.
_FAKE_DEVICE_ID = "DEV-FAKE"
_FAKE_ORDER_ID = "ORD-FAKE"


def _reserve_device_fake(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "reserved": True,
        "device": {
            "id": _FAKE_DEVICE_ID,
            "employee_id": args.get("employee_id"),
            "sku": args.get("sku"),
            "status": "assigned",
        },
    }


def _request_replacement_fake(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "order": {
            "id": _FAKE_ORDER_ID,
            "employee_id": args.get("employee_id"),
            "sku": args.get("sku"),
            "status": "ordered",
            "eta": None,
        },
    }


FakeShape = Callable[[dict[str, Any]], dict[str, Any]]

_DEFAULT_FAKE_SHAPES: dict[str, FakeShape] = {
    "reserve_device": _reserve_device_fake,
    "request_replacement": _request_replacement_fake,
}


def is_read_tool(name: str) -> bool:
    """Return True when ``name`` matches the DEC-05 read-tool vocabulary."""
    return name.startswith(_READ_TOOL_PREFIXES)


def _is_recognized_mutation_tool(name: str) -> bool:
    return name.startswith(_MUTATION_TOOL_PREFIXES)


def validate_fault_target(tool: str) -> None:
    """Raise :class:`ScenarioConfigError` for a DEC-05-illegal fault target.

    Mutation tools are those matching ``reserve_*`` / ``request_*`` /
    ``replace_*`` or any other tool the scenario explicitly declares in
    ``faults[].tool``. Reads are never legal fault targets.
    """
    if is_read_tool(tool):
        raise ScenarioConfigError(
            f"fault target {tool!r} is a read tool; DEC-05 refuses faults on reads"
        )
    if not _is_recognized_mutation_tool(tool):
        # A non-pattern tool is allowed only because the scenario author
        # explicitly declared it in faults[].tool; nothing further to check.
        return


def arm_fault(tool: str, kind: str) -> None:
    """Arm a fault for ``tool`` so the next matching call is intercepted."""
    _context().active[tool] = kind


def clear_faults() -> None:
    """Reset the fault registry and the applied-fault record (engine entrypoint)."""
    ctx = _context()
    ctx.active.clear()
    ctx.applied.clear()


def snapshot_applied_faults() -> list[dict[str, Any]]:
    """Return a copy of the faults actually applied during the current run."""
    return list(_context().applied)


def _apply_fault(
    tool_name: str,
    args: dict[str, Any],
    fake_shapes: dict[str, FakeShape],
) -> dict[str, Any] | None:
    """Return a short-circuit result, or raise, for an armed fault.

    Returning a dict short-circuits the real tool call (ADK behaviour); raising
    makes the agent observe a tool error. ``None`` means "no fault active".
    """
    ctx = _context()
    kind = ctx.active.get(tool_name)
    if kind is None:
        return None

    ctx.applied.append({"tool": tool_name, "kind": kind})

    if kind == "timeout":
        raise TimeoutError(f"tool {tool_name!r} timed out")
    if kind == "http_500":
        raise RuntimeError("HTTP 500")
    if kind == "stale":
        raise RuntimeError("STALE_RESPONSE; the previous result is stale, retry the call")
    if kind == "success_without_state_change":
        return fake_shapes[tool_name](args)
    raise ScenarioConfigError(f"unknown fault kind {kind!r}")


def build_fault_callbacks(
    faults: list[ScenarioFault],
    tool_fake_shapes: dict[str, FakeShape] | None = None,
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Build ``(before_tool_callback, after_tool_callback)`` for the faults.

    DEC-05 is enforced here at build time: every fault target is validated and
    every ``success_without_state_change`` fault must have a defined fake shape.
    The returned callbacks read the current run's fault registry, so the engine
    controls *when* each fault is active by arming it on schedule.
    """
    for fault in faults:
        validate_fault_target(fault.tool)

    fake_shapes = {**_DEFAULT_FAKE_SHAPES, **(tool_fake_shapes or {})}
    for fault in faults:
        if fault.kind == "success_without_state_change" and fault.tool not in fake_shapes:
            raise ScenarioConfigError(
                f"no fake success shape defined for fault target {fault.tool!r}"
            )

    async def before_tool_callback(
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
    ) -> dict[str, Any] | None:
        del tool_context  # fault injection needs only the tool name + arguments
        return _apply_fault(tool.name, args, fake_shapes)

    async def after_tool_callback(
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        del tool, args, tool_context, result  # none of the four kinds mutate results
        return None

    return before_tool_callback, after_tool_callback
