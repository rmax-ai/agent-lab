"""Fault-state concurrency isolation tests (DEC-05, SPEC §17.2).

Before the contextvars refactor, fault state lived in module globals: two
concurrent :class:`ScenarioEngine` runs armed, cleared, and recorded faults
into one shared registry and cross-contaminated. These tests pin the per-run
isolation (concurrent ``asyncio.gather`` runs) and the unchanged sequential
semantics of the public arm/clear/snapshot API.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agentlab.backend.scenarios import ScenarioEngine
from agentlab.backend.scenarios import faults as faults_module
from agentlab.backend.scenarios.models import Scenario, ScenarioExpected, ScenarioFault
from agentlab.world import db as world_db
from agentlab.world.app import create_app as create_world_app

_TOKEN = "test-token"
_PROBE_BUDGET = 400  # 400 * 0.005s = 2s worst-case wait for the arming timer


@pytest.fixture
def world_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "world.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENTLAB_SIMULATION_TOKEN", _TOKEN)
    world_db.reset_engine()
    return create_world_app()


def _transport(app: FastAPI) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app)


class _FaultProbingAgent:
    """Agent that calls one mutation tool until the armed fault fires.

    The engine arms each scenario's fault at ``at=0`` on its own task; polling
    here makes the probe deterministic regardless of which run's scheduler
    ticks first. The observed outcome (raised error or fake result) plus the
    run's applied-fault record are what the assertions check.
    """

    def __init__(
        self,
        fault_callbacks: tuple[Any, Any],
        tool: str,
        args: dict[str, Any],
    ) -> None:
        self._before_cb = fault_callbacks[0]
        self._tool = SimpleNamespace(name=tool)
        self._args = args
        self.observed: Any = None
        self.observed_error: BaseException | None = None
        self.timeline_events: list[str] = []
        self.final_state: str | None = None

    async def run(self, user_message: str) -> str:
        del user_message
        for _ in range(_PROBE_BUDGET):
            try:
                result = await self._before_cb(self._tool, self._args, None)
            except Exception as exc:  # the probe records whatever fault fires
                self.observed_error = exc
                return "faulted"
            if result is not None:
                self.observed = result
                return "faked"
            await asyncio.sleep(0.005)
        raise AssertionError(f"fault for {self._tool.name!r} never fired")


def _probing_factory(
    holder: dict[str, _FaultProbingAgent],
    tool: str,
    args: dict[str, Any],
) -> Any:
    def make(fault_callbacks: tuple[Any, Any]) -> tuple[_FaultProbingAgent, None]:
        agent = _FaultProbingAgent(fault_callbacks, tool, args)
        holder["agent"] = agent
        return agent, None

    return make


def _fault_scenario(scenario_id: str, tool: str, kind: str) -> Scenario:
    return Scenario(
        id=scenario_id,
        faults=[ScenarioFault(at=0.0, tool=tool, kind=kind)],
        expected=ScenarioExpected(),
    )


async def test_concurrent_runs_isolate_fault_state(world_app: FastAPI) -> None:
    """Two gathered runs arm different faults; neither sees the other's.

    The world's simulation endpoints serialize per-process (mutation lock), so
    both runs share one world instance safely; the fault arming + agent phases
    still overlap, which is the isolation property under test."""
    holder_a: dict[str, _FaultProbingAgent] = {}
    holder_b: dict[str, _FaultProbingAgent] = {}
    engine = ScenarioEngine()

    run_a = engine.run(
        _fault_scenario("iso-a", "reserve_device", "timeout"),
        _probing_factory(holder_a, "reserve_device", {"employee_id": "E1", "sku": "s-a"}),
        {"transport": _transport(world_app), "base_url": "http://mockworld"},
    )
    run_b = engine.run(
        _fault_scenario("iso-b", "request_replacement", "success_without_state_change"),
        _probing_factory(
            holder_b, "request_replacement", {"employee_id": "E2", "sku": "s-b"}
        ),
        {"transport": _transport(world_app), "base_url": "http://mockworld"},
    )

    result_a, result_b = await asyncio.gather(run_a, run_b)

    # Each run's applied-fault record contains ONLY its own fault.
    assert result_a.scenario_id == "iso-a"
    assert result_a.faults_applied == [{"tool": "reserve_device", "kind": "timeout"}]
    assert result_b.scenario_id == "iso-b"
    assert result_b.faults_applied == [
        {"tool": "request_replacement", "kind": "success_without_state_change"}
    ]

    # Each agent observed its own fault's behaviour, not the other run's:
    # A saw the timeout raise, B saw the fake success short-circuit.
    assert isinstance(holder_a["agent"].observed_error, TimeoutError)
    assert holder_a["agent"].observed is None
    assert holder_b["agent"].observed_error is None
    assert holder_b["agent"].observed == {
        "order": {
            "id": "ORD-FAKE",
            "employee_id": "E2",
            "sku": "s-b",
            "status": "ordered",
            "eta": None,
        }
    }


async def test_sequential_runs_do_not_leak_fault_state(world_app: FastAPI) -> None:
    """A second run starts clean: the first run's armed fault must not leak in."""
    holder: dict[str, _FaultProbingAgent] = {}
    engine = ScenarioEngine()
    run_kwargs = {"transport": _transport(world_app), "base_url": "http://mockworld"}

    first = await engine.run(
        _fault_scenario("seq-1", "reserve_device", "timeout"),
        _probing_factory(holder, "reserve_device", {"employee_id": "E1", "sku": "s"}),
        run_kwargs,
    )
    assert first.faults_applied == [{"tool": "reserve_device", "kind": "timeout"}]

    # The second run declares NO faults. Its agent probes the same tool once:
    # if run 1's armed timeout had leaked into run 2's context, this call
    # would raise; a clean context returns None (no fault active).
    class _CleanAgent:
        def __init__(self, fault_callbacks: tuple[Any, Any]) -> None:
            self._before_cb = fault_callbacks[0]
            self.probe_result: Any = "not-run"
            self.timeline_events: list[str] = []
            self.final_state: str | None = None

        async def run(self, user_message: str) -> str:
            del user_message
            self.probe_result = await self._before_cb(
                SimpleNamespace(name="reserve_device"), {"employee_id": "E1", "sku": "s"}, None
            )
            return "done"

    clean_holder: dict[str, _CleanAgent] = {}

    def make_clean(fault_callbacks: tuple[Any, Any]) -> tuple[_CleanAgent, None]:
        agent = _CleanAgent(fault_callbacks)
        clean_holder["agent"] = agent
        return agent, None

    second = await engine.run(
        Scenario(id="seq-2", expected=ScenarioExpected()), make_clean, run_kwargs
    )
    assert clean_holder["agent"].probe_result is None
    assert second.faults_applied == []

    # And outside any run, the module-level state is untouched by either run.
    assert faults_module.snapshot_applied_faults() == []


async def test_run_contexts_isolate_without_the_engine() -> None:
    """Two tasks inside ``run_context`` never share armed or applied faults."""

    async def exercise(tool: str, kind: str) -> list[dict[str, Any]]:
        with faults_module.run_context():
            faults_module.clear_faults()
            faults_module.arm_fault(tool, kind)
            await asyncio.sleep(0.01)  # let the sibling task overlap
            before_cb, _ = faults_module.build_fault_callbacks(
                [ScenarioFault(at=0.0, tool=tool, kind=kind)]
            )
            if kind == "timeout":
                with pytest.raises(TimeoutError):
                    await before_cb(SimpleNamespace(name=tool), {}, None)
            else:
                await before_cb(SimpleNamespace(name=tool), {}, None)
            return faults_module.snapshot_applied_faults()

    applied_a, applied_b = await asyncio.gather(
        exercise("reserve_device", "timeout"),
        exercise("request_replacement", "success_without_state_change"),
    )

    assert applied_a == [{"tool": "reserve_device", "kind": "timeout"}]
    assert applied_b == [
        {"tool": "request_replacement", "kind": "success_without_state_change"}
    ]


async def test_module_level_api_is_unchanged_outside_runs() -> None:
    """Sequential arm/apply/clear against the module API behaves as before."""
    faults_module.clear_faults()
    faults_module.arm_fault("reserve_device", "http_500")
    before_cb, _ = faults_module.build_fault_callbacks(
        [ScenarioFault(at=0.0, tool="reserve_device", kind="http_500")]
    )

    with pytest.raises(RuntimeError, match="HTTP 500"):
        await before_cb(SimpleNamespace(name="reserve_device"), {}, None)

    assert faults_module.snapshot_applied_faults() == [
        {"tool": "reserve_device", "kind": "http_500"}
    ]

    faults_module.clear_faults()
    assert faults_module.snapshot_applied_faults() == []
    # Cleared registry: the same call is a no-op now.
    assert await before_cb(SimpleNamespace(name="reserve_device"), {}, None) is None
