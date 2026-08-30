"""DEC-05 tool-fault injection callbacks (SPEC §17.2).

Tool faults may target ONLY mutation tools. A tool is a mutation target when
its name matches ``reserve_*`` / ``request_*`` / ``replace_*`` or when the
scenario author explicitly names it in ``faults[].tool``. Reads
(``get_*`` / ``check_*`` / ``verify_*``) are REFUSED as fault targets because
faulting a truthful read corrupts the evaluation baseline (DEC-05).

The callbacks read module-level state that the :class:`ScenarioEngine` drives
via :func:`arm_fault` / :func:`clear_faults`, so a single fault can be armed at
its scheduled time and observed by whatever ADK agent is running.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentlab.backend.scenarios.models import ScenarioConfigError, ScenarioFault

_READ_TOOL_PREFIXES = ("get_", "check_", "verify_")
_MUTATION_TOOL_PREFIXES = ("reserve_", "request_", "replace_")

# Module-level fault state: the engine arms faults on schedule; the callbacks
# below read it on every tool invocation.
_active_faults: dict[str, str] = {}
_applied_faults: list[dict[str, Any]] = []

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
    _active_faults[tool] = kind


def clear_faults() -> None:
    """Reset the fault registry and the applied-fault record (engine entrypoint)."""
    _active_faults.clear()
    _applied_faults.clear()


def snapshot_applied_faults() -> list[dict[str, Any]]:
    """Return a copy of the faults actually applied during the current run."""
    return list(_applied_faults)


def _apply_fault(
    tool_name: str,
    args: dict[str, Any],
    fake_shapes: dict[str, FakeShape],
) -> dict[str, Any] | None:
    """Return a short-circuit result, or raise, for an armed fault.

    Returning a dict short-circuits the real tool call (ADK behaviour); raising
    makes the agent observe a tool error. ``None`` means "no fault active".
    """
    kind = _active_faults.get(tool_name)
    if kind is None:
        return None

    _applied_faults.append({"tool": tool_name, "kind": kind})

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
    The returned callbacks read the module-level fault registry, so the engine
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
