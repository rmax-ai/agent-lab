"""Unit tests for the scenario engine (SPEC §16/§17 kind 1+2).

The engine drives MockWorld over HTTP (an in-process ``httpx.ASGITransport``)
and a stub agent; no real LLM is ever invoked. Fault semantics are exercised
directly against the DEC-05 callbacks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from google.adk.tools import FunctionTool

from agentlab.backend.scenarios import ScenarioEngine, load_scenario
from agentlab.backend.scenarios import faults as faults_module
from agentlab.backend.scenarios.models import (
    Scenario,
    ScenarioConfigError,
    ScenarioExpected,
    ScenarioFault,
)
from agentlab.world import db as world_db
from agentlab.world.app import create_app as create_world_app

_TOKEN = "test-token"


@pytest.fixture
def world_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "world.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENTLAB_SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("ALLOWED_DOMAINS", "device-agent:devices")
    world_db.reset_engine()
    return create_world_app()


def _transport(app: FastAPI) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app)


async def _get_inventory(app: FastAPI) -> dict[str, int]:
    async with httpx.AsyncClient(
        transport=_transport(app), base_url="http://mockworld"
    ) as client:
        response = await client.get(
            "/world/devices/inventory", headers={"X-Agent-Id": "device-agent"}
        )
        response.raise_for_status()
        return {item["sku"]: item["available"] for item in response.json()}


class _NoopAgent:
    """A stub agent that sleeps then returns; exposes harness attributes."""

    def __init__(self, sleep_seconds: float = 0.0) -> None:
        self._sleep_seconds = sleep_seconds
        self.timeline_events: list[str] = []
        self.final_state: str | None = None

    async def run(self, user_message: str) -> str:
        del user_message
        await asyncio.sleep(self._sleep_seconds)
        return "done"


def _factory(
    sleep_seconds: float = 0.0,
) -> Callable[[tuple[Any, Any]], tuple[_NoopAgent, asyncio.Event]]:
    def make(fault_callbacks: tuple[Any, Any]) -> tuple[_NoopAgent, asyncio.Event]:
        del fault_callbacks
        return _NoopAgent(sleep_seconds), asyncio.Event()

    return make


async def test_reset_and_load_apply_initial_state(world_app: FastAPI) -> None:
    engine = ScenarioEngine()
    scenario = Scenario(
        id="reset-load",
        initial_state={"inventory.macbook_air_15.available": 0},
        expected=ScenarioExpected(),
    )

    result = await engine.run(
        scenario,
        _factory(0.0),
        {"transport": _transport(world_app), "base_url": "http://mockworld"},
    )

    assert "reset" in {entry["kind"] for entry in result.timeline}
    assert "load" in {entry["kind"] for entry in result.timeline}
    assert (await _get_inventory(world_app))["macbook_air_15"] == 0


async def test_timed_mutation_fires_at_or_after_ts(world_app: FastAPI) -> None:
    engine = ScenarioEngine()
    scenario = Scenario(
        id="timed-mutation",
        events=[
            {
                "at": 0.05,
                "mutate": {"inventory.macbook_air_15.available": 5},
            }
        ],
        expected=ScenarioExpected(),
    )

    # The stub agent sleeps past the 0.05s mutation so the timer fires first.
    result = await engine.run(
        scenario,
        _factory(0.15),
        {"transport": _transport(world_app), "base_url": "http://mockworld"},
    )

    mutations = [entry for entry in result.timeline if entry["kind"] == "mutate"]
    assert mutations
    assert mutations[0]["detail"]["at"] == 0.05
    assert (await _get_inventory(world_app))["macbook_air_15"] == 5


def test_load_scenario_refuses_read_tool_fault_target(tmp_path: Path) -> None:
    path = tmp_path / "bad-fault.yaml"
    path.write_text(
        "id: bad-fault\n"
        "faults:\n"
        "  - at: 1\n"
        "    tool: check_inventory\n"
        "    kind: timeout\n"
        "expected:\n"
        "  required_events: []\n"
        "  allowed_final_states: []\n"
        "  forbidden_events: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ScenarioConfigError):
        load_scenario(path)


def test_build_fault_callbacks_refuses_read_tool_target() -> None:
    with pytest.raises(ScenarioConfigError):
        faults_module.build_fault_callbacks(
            [ScenarioFault(at=0.0, tool="verify_account", kind="stale")]
        )


def test_load_scenario_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ScenarioConfigError):
        load_scenario(tmp_path / "does-not-exist.yaml")


def test_load_scenario_invalid_schema(tmp_path: Path) -> None:
    path = tmp_path / "bad-schema.yaml"
    path.write_text("id: 42\n", encoding="utf-8")
    with pytest.raises(ScenarioConfigError):
        load_scenario(path)


@pytest.mark.parametrize(
    ("kind", "exc_type", "message"),
    [
        ("timeout", TimeoutError, None),
        ("http_500", RuntimeError, "HTTP 500"),
        ("stale", RuntimeError, "STALE_RESPONSE"),
    ],
)
async def test_error_faults_raise_in_before_callback(
    kind: str,
    exc_type: type[BaseException],
    message: str | None,
) -> None:
    called: list[str] = []

    async def reserve_device(employee_id: str, sku: str) -> dict[str, Any]:
        called.append(sku)
        return {"reserved": True, "device": {"sku": sku, "employee_id": employee_id}}

    before_cb, _ = faults_module.build_fault_callbacks(
        [ScenarioFault.model_validate({"at": 0.0, "tool": "reserve_device", "kind": kind})]
    )
    faults_module.clear_faults()
    faults_module.arm_fault("reserve_device", kind)

    with pytest.raises(exc_type) as exc_info:
        await before_cb(
            FunctionTool(reserve_device),
            {"employee_id": "E42", "sku": "macbook_pro_14"},
            None,
        )

    if message is not None:
        assert message in str(exc_info.value)
    assert called == []  # the real tool never executed


async def test_success_without_state_change_short_circuits() -> None:
    called: list[str] = []

    async def reserve_device(employee_id: str, sku: str) -> dict[str, Any]:
        called.append(sku)
        return {"reserved": True, "device": {"sku": sku, "employee_id": employee_id}}

    kind = "success_without_state_change"
    before_cb, _ = faults_module.build_fault_callbacks(
        [ScenarioFault.model_validate({"at": 0.0, "tool": "reserve_device", "kind": kind})]
    )
    faults_module.clear_faults()
    faults_module.arm_fault("reserve_device", kind)

    result = await before_cb(
        FunctionTool(reserve_device),
        {"employee_id": "E42", "sku": "macbook_pro_14"},
        None,
    )

    assert result == {
        "reserved": True,
        "device": {
            "id": "DEV-FAKE",
            "employee_id": "E42",
            "sku": "macbook_pro_14",
            "status": "assigned",
        },
    }
    assert called == []  # the real reserve never reached MockWorld

    applied = faults_module.snapshot_applied_faults()
    assert applied == [{"tool": "reserve_device", "kind": kind}]


def test_success_without_state_change_requires_fake_shape() -> None:
    with pytest.raises(ScenarioConfigError):
        faults_module.build_fault_callbacks(
            [ScenarioFault(at=0.0, tool="provision_account", kind="success_without_state_change")]
        )
