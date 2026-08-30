"""Device certification pack runner (SPEC §18, A.13).

Parametrized over ``scenarios/devices/01`` .. ``05``. Each scenario runs a
scripted device agent — a deterministic canned trajectory per scenario, no
real LLM and no network beyond in-process ASGI — that drives the REAL
MockWorld device tools and the REAL backend case/workflow/human-task APIs.
The ScenarioEngine plays the world (reset → load → timed mutations); the
EvaluationEngine scores the run against the SPEC §24 weights. Every pack
scenario must PASS (score ≥ threshold).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agentlab.backend import db as backend_db
from agentlab.backend.app import create_app as create_backend_app
from agentlab.backend.evaluation import EvaluationEngine
from agentlab.backend.evaluation.scoring import ScenarioScore
from agentlab.backend.scenarios import ScenarioEngine, load_scenario
from agentlab.backend.scenarios.engine import ScenarioResult
from agentlab.backend.scenarios.models import Scenario
from agentlab.world import db as world_db
from agentlab.world.app import create_app as create_world_app

from ..agent import build_device_agent
from ..tools import device

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCENARIOS_DIR = _REPO_ROOT / "scenarios" / "devices"

# The SPEC §18 device certification pack, in order.
PACK_SCENARIOS = [
    "01_happy_path.yaml",
    "02_missing_location.yaml",
    "03_no_inventory.yaml",
    "04_delivery_failure.yaml",
    "05_replacement_requires_approval.yaml",
]

_TOKEN = "test-token"
_EMPLOYEE_ID = "E42"
_MANAGER_ID = "M1"
_UNAUTHORIZED_ACTOR = "unknown-actor"  # placeholder; never requested_from (DEC-10)
_RUN_CASE_ID = "ONB-E42"
_COORDINATOR_ID = "onboarding-coordinator"
_TIME_SCALE = 0.02  # the t=30 mutations land at ~0.6s in test time

# Expected final per-SKU availability, keyed by scenario id. The reservation /
# substitution trajectories above determine each delta from the seeded stock
# (macbook_pro_14: 1, macbook_air_15: 7).
_EXPECTED_AVAILABLE: dict[str, dict[str, int]] = {
    "device-01-happy-path": {"macbook_pro_14": 0, "macbook_air_15": 7},
    "device-02-missing-location": {"macbook_pro_14": 0, "macbook_air_15": 7},
    "device-03-no-inventory": {"macbook_pro_14": 0, "macbook_air_15": 6},
    "device-04-delivery-failure": {"macbook_pro_14": 0, "macbook_air_15": 7},
    "device-05-replacement-requires-approval": {"macbook_pro_14": 0, "macbook_air_15": 7},
}


class ScriptedPackAgent:
    """A canned device-agent trajectory per certification scenario.

    Records the snake_case trajectory events the scenario expects (see the
    vocabulary in ``scenarios/README.md``) while exercising the real tools and
    backend routes. ``final_state`` / ``timeline_events`` are the attributes
    the ScenarioEngine harness reads back.
    """

    def __init__(
        self,
        scenario_id: str,
        backend: httpx.AsyncClient,
        mode: str = "pass",
    ) -> None:
        self.scenario_id = scenario_id
        self.backend = backend
        self.mode = mode
        self.case_id = _RUN_CASE_ID
        self.workflow_id = f"WF-{scenario_id}"
        self.timeline_events: list[str] = []
        self.final_state: str | None = None
        self.case_ids: list[str] = []

    def _record(self, event: str) -> None:
        self.timeline_events.append(event)

    async def run(self, user_message: str) -> str:
        """Entry point used by the ScenarioEngine."""
        del user_message
        await self._open_workflow()
        await self._drive()
        self.case_ids = [self.case_id] * len(self.timeline_events)
        return "done"

    # --- backend contract helpers --------------------------------------------

    async def _open_workflow(self) -> None:
        """Create the case, accept the WorkflowRequest, and ack ownership."""
        response = await self.backend.post(
            "/cases",
            json={"case_id": self.case_id, "employee_id": _EMPLOYEE_ID, "context": {}},
        )
        assert response.status_code == 201, response.text
        response = await self.backend.post(
            "/workflows",
            json={
                "workflow_id": self.workflow_id,
                "case_id": self.case_id,
                "goal": "employee_device_ready",
                "employee_id": _EMPLOYEE_ID,
                "context": {},
                "target_agent_id": "device-agent",
            },
            headers={"X-Agent-Id": _COORDINATOR_ID},
        )
        assert response.status_code == 201, response.text
        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/ack",
            headers={"X-Agent-Id": "device-agent"},
        )
        assert response.status_code == 200, response.text

    async def _create_task(self, task_type: str, context: dict[str, Any]) -> str:
        """Persist a HumanTask for this workflow and record the event."""
        task_id = f"HT-{self.scenario_id}"
        response = await self.backend.post(
            "/tasks",
            json={
                "human_task_id": task_id,
                "case_id": self.case_id,
                "workflow_id": self.workflow_id,
                "requested_by": "device-agent",
                "requested_from": _MANAGER_ID,
                "type": task_type,
                "context": context,
                "allowed_actions": ["approve", "reject"],
                "status": "open",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        self._record("human_task_created")
        return task_id

    async def _wait_for_human(self) -> None:
        """Report WAITING_FOR_HUMAN so the decision can resume the workflow."""
        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/status",
            json={
                "workflow_id": self.workflow_id,
                "status": "waiting_for_human",
                "blockers": [],
            },
            headers={"X-Agent-Id": "device-agent"},
        )
        assert response.status_code == 200, response.text

    async def _decide(
        self,
        task_id: str,
        decision: dict[str, Any],
        resolved_by: str,
    ) -> httpx.Response:
        """Post a human decision on the task (scripted human behavior)."""
        return await self.backend.post(
            f"/tasks/{task_id}/decision",
            json={"decision": decision, "resolved_by": resolved_by},
        )

    async def _complete_verified(self) -> None:
        """Report COMPLETED with verified=true and record outcome_verified."""
        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/complete",
            json={"verified": True},
            headers={"X-Agent-Id": "device-agent"},
        )
        assert response.status_code == 200, response.text
        self._record("outcome_verified")
        self.final_state = "completed"

    # --- world helpers ---------------------------------------------------------

    async def _wait_for_exhaustion(self) -> None:
        """Poll inventory until the t=30 exhaustion mutation has landed."""
        for _ in range(300):  # 300 * 0.01s = 3s real-time budget
            summary = await device.check_inventory(_EMPLOYEE_ID)
            if summary.get("available", {}).get("macbook_pro_14") == 0:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("timed inventory mutation never landed")

    async def _wait_for_delivery_failure(self) -> None:
        """Poll the assignment until the t=30 delivery-failure mutation lands."""
        for _ in range(300):  # 300 * 0.01s = 3s real-time budget
            assignment = await device.get_device_assignment(_EMPLOYEE_ID)
            device_row = assignment.get("assigned_device") or {}
            if device_row.get("status") == "delivery_failed":
                return
            await asyncio.sleep(0.01)
        raise AssertionError("timed delivery-failure mutation never landed")

    async def _reserve_standard(self) -> None:
        """Check inventory and reserve the standard SKU."""
        inventory = await device.check_inventory(_EMPLOYEE_ID)
        self._record("inventory_checked")
        assert inventory["available"]["macbook_pro_14"] >= 1
        result = await device.reserve_device(_EMPLOYEE_ID, "macbook_pro_14")
        assert result["reserved"] is True, result
        self._record("device_reserved")

    # --- per-scenario trajectories ---------------------------------------------

    async def _drive(self) -> None:
        requirements = await device.get_employee_device_requirements(_EMPLOYEE_ID)
        assert "error" not in requirements, requirements
        drivers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "device-01-happy-path": self._drive_happy_path,
            "device-02-missing-location": self._drive_missing_location,
            "device-03-no-inventory": self._drive_no_inventory,
            "device-04-delivery-failure": self._drive_delivery_failure,
            "device-05-replacement-requires-approval": self._drive_replacement_approval,
        }
        driver = drivers.get(self.scenario_id)
        assert driver is not None, f"no scripted trajectory for {self.scenario_id}"
        await driver(requirements)

    async def _drive_happy_path(self, requirements: dict[str, Any]) -> None:
        assert requirements["location"] == "Amsterdam"
        await self._reserve_standard()
        assignment = await device.get_device_assignment(_EMPLOYEE_ID)
        assert assignment["assigned_device"] is not None
        self._record("delivery_verified")
        await self._complete_verified()

    async def _drive_missing_location(self, requirements: dict[str, Any]) -> None:
        if not requirements.get("location"):
            # Location policy: never guess a delivery address; ask a human.
            self._record("location_missing_detected")
            task_id = await self._create_task(
                "MISSING_INFORMATION",
                {"reason": "address_confirmation", "employee_id": _EMPLOYEE_ID},
            )
            await self._wait_for_human()
            response = await self._decide(
                task_id,
                {"decision": "approve", "address": "1 Placeholder Lane, Amsterdam"},
                _MANAGER_ID,
            )
            assert response.status_code == 200, response.text
            self._record("address_confirmed")
        await self._reserve_standard()
        assignment = await device.get_device_assignment(_EMPLOYEE_ID)
        assert assignment["assigned_device"] is not None
        self._record("delivery_verified")
        await self._complete_verified()

    async def _drive_no_inventory(self, requirements: dict[str, Any]) -> None:
        del requirements
        inventory = await device.check_inventory(_EMPLOYEE_ID)
        self._record("inventory_checked")
        await self._wait_for_exhaustion()
        inventory = await device.check_inventory(_EMPLOYEE_ID)
        assert inventory["available"]["macbook_pro_14"] == 0

        if self.mode == "fail":
            # Unsafe trajectory: reserve the exhausted SKU anyway.
            result = await device.reserve_device(_EMPLOYEE_ID, "macbook_pro_14")
            assert result["reserved"] is False
            assert result["code"] == "NO_INVENTORY"
            self._record("unavailable_device_reserved")
            self.final_state = "failed"
            return

        self._record("no_inventory_detected")
        assert inventory["available"]["macbook_air_15"] >= 1
        # Substitution policy: MacBook Air 15 only with manager approval.
        task_id = await self._create_task(
            "APPROVAL",
            {"reason": "substitute macbook_air_15 for exhausted macbook_pro_14"},
        )
        await self._wait_for_human()
        response = await self._decide(task_id, {"decision": "approve"}, _MANAGER_ID)
        assert response.status_code == 200, response.text
        self._record("approval_granted")
        result = await device.reserve_device(_EMPLOYEE_ID, "macbook_air_15")
        assert result["reserved"] is True, result
        self._record("substitute_reserved")
        assignment = await device.get_device_assignment(_EMPLOYEE_ID)
        assert assignment["assigned_device"]["sku"] == "macbook_air_15"
        await self._complete_verified()

    async def _drive_delivery_failure(self, requirements: dict[str, Any]) -> None:
        del requirements
        await self._reserve_standard()
        await self._wait_for_delivery_failure()
        self._record("delivery_failure_detected")
        replacement = await device.request_replacement(_EMPLOYEE_ID, "delivery failed")
        assert "order" in replacement, replacement
        self._record("replacement_requested")
        assignment = await device.get_device_assignment(_EMPLOYEE_ID)
        assert assignment["order"]["status"] == "ordered"
        await self._complete_verified()

    async def _drive_replacement_approval(self, requirements: dict[str, Any]) -> None:
        del requirements
        await self._reserve_standard()
        await self._wait_for_delivery_failure()
        self._record("delivery_failure_detected")
        replacement = await device.request_replacement(_EMPLOYEE_ID, "delivery failed")
        assert "order" in replacement, replacement
        self._record("replacement_requested")
        task_id = await self._create_task(
            "APPROVAL",
            {"reason": "replacement approval for failed delivery"},
        )
        await self._wait_for_human()
        # DEC-10: an unauthorized resolver is rejected with 403.
        unauthorized = await self._decide(
            task_id, {"decision": "approve"}, _UNAUTHORIZED_ACTOR
        )
        assert unauthorized.status_code == 403, unauthorized.text
        self._record("unauthorized_approval_rejected")
        authorized = await self._decide(task_id, {"decision": "approve"}, _MANAGER_ID)
        assert authorized.status_code == 200, authorized.text
        self._record("replacement_approved")
        assignment = await device.get_device_assignment(_EMPLOYEE_ID)
        assert assignment["order"]["status"] == "ordered"
        await self._complete_verified()


@pytest.fixture
def world_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build MockWorld + the backend over one temp shared SQLite file."""
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "lab.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENTLAB_SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("ALLOWED_DOMAINS", "device-agent:devices")
    monkeypatch.delenv("ALLOW_ANY_RESOLVER", raising=False)  # enforce DEC-10
    # The device tools read MOCKWORLD_URL at import time; the host is ignored
    # by the ASGI transport, but keep it stable so routing stays local.
    monkeypatch.setenv("MOCKWORLD_URL", "http://mockworld")
    world_db.reset_engine()
    backend_db.reset_engine()
    return create_world_app()


@pytest.fixture
def backend_app(world_app: FastAPI) -> FastAPI:
    """Build the backend app against the same temp database."""
    del world_app  # the env + engine reset happen in the world_app fixture
    return create_backend_app()


@pytest.fixture
def device_transport(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> httpx.ASGITransport:
    """Route the device tools' HTTP calls at the in-process MockWorld app."""
    transport = httpx.ASGITransport(app=world_app)
    monkeypatch.setattr(device, "TRANSPORT", transport)
    return transport


@pytest.fixture
async def backend_client(backend_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An async client for the in-process backend app."""
    transport = httpx.ASGITransport(app=backend_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://backend"
    ) as client:
        yield client


async def run_pack_scenario(
    scenario_file: str,
    world_app: FastAPI,
    backend: httpx.AsyncClient,
    mode: str = "pass",
) -> tuple[Scenario, ScenarioResult, ScenarioScore, ScriptedPackAgent]:
    """Run one pack scenario through the engines and return the score."""
    scenario = load_scenario(_SCENARIOS_DIR / scenario_file)
    holder: dict[str, ScriptedPackAgent] = {}

    def factory(fault_callbacks: tuple[Any, Any]) -> tuple[ScriptedPackAgent, asyncio.Event]:
        del fault_callbacks  # no faults in the certification pack
        agent = ScriptedPackAgent(scenario.id, backend, mode)
        holder["agent"] = agent
        return agent, asyncio.Event()

    result = await ScenarioEngine().run(
        scenario,
        factory,
        {
            "transport": httpx.ASGITransport(app=world_app),
            "base_url": "http://mockworld",
            "time_scale": _TIME_SCALE,
        },
    )
    available = (await device.check_inventory(_EMPLOYEE_ID)).get("available", {})
    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state=available,
        expected_state=_EXPECTED_AVAILABLE[scenario.id],
        retry_count=0,
        delegation_depth=0,
        case_ids=holder["agent"].case_ids,
        run_case_id=_RUN_CASE_ID,
    )
    return scenario, result, score, holder["agent"]


@pytest.mark.parametrize("scenario_file", PACK_SCENARIOS)
async def test_certification_pack_passes(
    world_app: FastAPI,
    device_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    scenario_file: str,
) -> None:
    """Every SPEC §18 pack scenario scores PASS against the §24 threshold."""
    del device_transport  # monkeypatched; used implicitly by the tools
    device_agent = build_device_agent()
    assert device_agent.id == "device-agent"

    (scenario, result, score, agent) = await run_pack_scenario(
        scenario_file, world_app, backend_client
    )

    assert score.passed is True, f"{scenario.id} failed: {score.model_dump_json()}"
    assert score.total >= score.threshold
    assert result.final_state in scenario.expected.allowed_final_states
    for event in scenario.expected.forbidden_events:
        assert event not in agent.timeline_events
