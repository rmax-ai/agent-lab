"""Deterministic scenario wire-up for the Device agent (A.11 + A.12).

This is the single place where the Device agent meets the backend scenario and
evaluation engines. No real LLM is invoked: a scripted agent drives the real
MockWorld tools (``check_inventory``, ``reserve_device``) over an in-process
``httpx.ASGITransport``, and the ScenarioEngine + EvaluationEngine assert the
SPEC §24/§25 outcome.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agentlab.backend.evaluation import EvaluationEngine
from agentlab.backend.evaluation.results import render_failed_diff
from agentlab.backend.scenarios import ScenarioEngine, load_scenario
from agentlab.world import db as world_db
from agentlab.world.app import create_app as create_world_app

from ..agent import build_device_agent
from ..tools import device

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCENARIOS_DIR = _REPO_ROOT / "scenarios" / "devices"
_TOKEN = "test-token"
_RUN_CASE_ID = "ONB-E42"
_TIME_SCALE = 0.02  # the 30s inventory mutation lands at ~0.6s in test time

_EXPECTED_AVAILABLE = {"macbook_pro_14": 0, "macbook_air_15": 7}


class ScriptedDeviceAgent:
    """A canned device-agent trajectory: check inventory, then act per scenario."""

    def __init__(self, scenario_id: str, mode: str) -> None:
        self.scenario_id = scenario_id
        self.mode = mode
        self.timeline_events: list[str] = []
        self.final_state: str | None = None
        self.case_ids: list[str] = []

    async def run(self, user_message: str) -> str:
        del user_message
        self._record("inventory_checked")
        await self._drive()
        self.case_ids = [_RUN_CASE_ID] * len(self.timeline_events)
        return "done"

    def _record(self, event: str) -> None:
        self.timeline_events.append(event)

    async def _drive(self) -> None:
        if self.scenario_id == "device-inventory-exhausted":
            await self._drive_exhausted()
        elif self.scenario_id == "device-happy-path":
            await self._drive_happy_path()
        else:
            self.final_state = "failed"

    async def _wait_for_exhaustion(self) -> None:
        for _ in range(200):  # 200 * 0.01s = 2s real-time budget
            summary = await device.check_inventory("E42")
            available = summary.get("available", {})
            if available.get("macbook_pro_14") == 0:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("timed inventory mutation never landed")

    async def _drive_exhausted(self) -> None:
        await self._wait_for_exhaustion()
        if self.mode == "pass":
            self._record("no_inventory_detected")
            self.final_state = "waiting_for_human"
        else:
            result = await device.reserve_device("E42", "macbook_pro_14")
            assert result["reserved"] is False  # NO_INVENTORY after the drop
            self._record("unavailable_device_reserved")
            self.final_state = "failed"

    async def _drive_happy_path(self) -> None:
        result = await device.reserve_device("E42", "macbook_pro_14")
        assert result["reserved"] is True
        self._record("device_reserved")
        self._record("outcome_verified")
        self.final_state = "completed"


@pytest.fixture
def world_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "world.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENTLAB_SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("ALLOWED_DOMAINS", "device-agent:devices")
    # The device tools read MOCKWORLD_URL at import time; host is ignored by the
    # ASGI transport, but keep it stable so a fresh client still routes locally.
    monkeypatch.setenv("MOCKWORLD_URL", "http://mockworld")
    world_db.reset_engine()
    return create_world_app()


@pytest.fixture
def device_transport(world_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> httpx.ASGITransport:
    transport = httpx.ASGITransport(app=world_app)
    monkeypatch.setattr(device, "TRANSPORT", transport)
    return transport


def _run_kwargs(app: FastAPI) -> dict[str, Any]:
    return {
        "transport": httpx.ASGITransport(app=app),
        "base_url": "http://mockworld",
        "time_scale": _TIME_SCALE,
    }


def _factory(scenario_id: str, mode: str, holder: dict[str, ScriptedDeviceAgent]):
    def make(fault_callbacks: tuple[Any, Any]) -> tuple[ScriptedDeviceAgent, asyncio.Event]:
        del fault_callbacks
        agent = ScriptedDeviceAgent(scenario_id, mode)
        holder["agent"] = agent
        return agent, asyncio.Event()

    return make


async def _run_and_evaluate(
    scenario_file: str,
    mode: str,
    world_app: FastAPI,
) -> tuple[Any, Any, Any, ScriptedDeviceAgent]:
    scenario = load_scenario(_SCENARIOS_DIR / scenario_file)
    holder: dict[str, ScriptedDeviceAgent] = {}
    result = await ScenarioEngine().run(
        scenario,
        _factory(scenario.id, mode, holder),
        _run_kwargs(world_app),
    )
    available = (await device.check_inventory("E42")).get("available", {})
    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state=available,
        expected_state=_EXPECTED_AVAILABLE,
        retry_count=0,
        delegation_depth=0,
        case_ids=holder["agent"].case_ids,
        run_case_id=_RUN_CASE_ID,
    )
    return scenario, result, score, holder["agent"]


@pytest.mark.parametrize(
    "scenario_file",
    [path.name for path in sorted(_SCENARIOS_DIR.glob("*.yaml"))],
)
async def test_device_scenarios_pass(
    world_app: FastAPI,
    device_transport: httpx.ASGITransport,
    scenario_file: str,
) -> None:
    del device_transport  # monkeypatched; used implicitly by the tools
    device_agent = build_device_agent()
    assert device_agent.id == "device-agent"

    (_, result, score, _) = await _run_and_evaluate(scenario_file, "pass", world_app)

    assert score.passed is True
    assert score.total >= score.threshold
    assert result.final_state in {
        "waiting_for_human",
        "completed",
    }


async def test_exhausted_fail_variant_shows_diff(
    world_app: FastAPI,
    device_transport: httpx.ASGITransport,
) -> None:
    del device_transport
    (scenario, result, score, _) = await _run_and_evaluate(
        "device-inventory-exhausted.yaml", "fail", world_app
    )

    assert score.passed is False
    assert score.total < score.threshold

    diff = render_failed_diff(
        scenario.id,
        scenario.expected.required_events,
        result.events,
        result.final_state,
    )
    assert "Expected: no_inventory_detected  Observed: none" in diff
