"""Integration scenario runner (SPEC §20, Epic C batch 1).

Parametrized over ``scenarios/integration/*.yaml``; new integration scenarios
drop in by adding a YAML plus a scripted driver below. Each scenario runs the
REAL onboarding coordinator (no live LLM — a canned ``before_model_callback``
answers any model turn) against the REAL backend over in-process ASGI, while
scripted device/access domain loops react to REAL ``WORKFLOW_DELEGATED``
events and do their work through the REAL MockWorld tools. Nothing a real
domain agent would do is stubbed. The ScenarioEngine plays the world (reset →
load → timed mutations); the EvaluationEngine scores the run against the
SPEC §24 weights. Every integration scenario must PASS.

World-setup note: ``initial_state`` follows the ``/simulation/load`` contract
(flat ``collection.<id>.<field>`` field mutations of existing rows — it never
creates rows), so the five integration employees, their identities, and
E103's assigned device + in-flight order are provisioned by this harness
(:func:`_provision_integration_world`) right after the engine's reset+load.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    # The device/access agents live in plain code directories (no
    # pyproject.toml); put the repo root on sys.path so the ``agents``
    # namespace package resolves, mirroring their own test conftests.
    sys.path.insert(0, str(_REPO_ROOT))

from agents.access.tools import access  # noqa: E402
from agents.device.tools import device  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402

import agentlab.onboarding.coordinator as coordinator_module  # noqa: E402
from agentlab.backend import db as backend_db  # noqa: E402
from agentlab.backend.app import create_app as create_backend_app  # noqa: E402
from agentlab.backend.evaluation import EvaluationEngine  # noqa: E402
from agentlab.backend.scenarios import ScenarioEngine, load_scenario  # noqa: E402
from agentlab.onboarding import CoordinatorAgent  # noqa: E402
from agentlab.world import db as world_db  # noqa: E402
from agentlab.world.app import create_app as create_world_app  # noqa: E402
from agentlab.world.models import Device, DeviceOrder, Employee, Identity  # noqa: E402

_SCENARIOS_DIR = _REPO_ROOT / "scenarios" / "integration"
_SCENARIO_FILES = sorted(_SCENARIOS_DIR.glob("*.yaml"))

_TOKEN = "test-token"
_TIME_SCALE = 0.02  # the t=30 mutation lands at ~0.6s, t=60 at ~1.2s

_DEVICE_AGENT = "device-agent"
_ACCESS_AGENT = "access-agent"
_COORDINATOR_ID = "onboarding-agent"
_MANAGER_ID = "M1"
_EMPLOYEES = ["E101", "E102", "E103", "E104", "E105"]
_CASES = {employee_id: f"ONB-{employee_id}" for employee_id in _EMPLOYEES}
_DELAYED_EMPLOYEE = "E103"  # in-flight order ORD-1 flips to delayed at t=30
_PRIVILEGED_EMPLOYEE = "E104"  # needs GRP-PRIVILEGED via manager approval
_STANDARD_GROUP = "GRP-STANDARD"
_PRIVILEGED_GROUP = "GRP-PRIVILEGED"
_STANDARD_SKU = "macbook_pro_14"
_START_DATE = "2026-08-31"  # Monday
_WORKFLOWS = ["device", "access"]
_READY_GOALS = {"employee_device_ready", "employee_access_ready"}


def _expected_state() -> dict[str, Any]:
    """The world state the run must reach, read back via truthful GETs."""
    state: dict[str, Any] = {
        # 5 stocked by initial_state, 4 reserved (E103's device pre-existed).
        "macbook_pro_14_available": 1,
    }
    for employee_id in _EMPLOYEES:
        state[f"{employee_id}_device"] = (
            "replacement_ordered" if employee_id == _DELAYED_EMPLOYEE else "assigned"
        )
        state[f"{employee_id}_grp_standard"] = "granted"
        state[f"{employee_id}_grp_privileged"] = (
            "granted" if employee_id == _PRIVILEGED_EMPLOYEE else "none"
        )
        state[f"{employee_id}_order"] = "none"
    # The world device summary returns the FIRST order row: E103's delayed
    # ORD-1. The replacement (ORD-2, ordered) is in flight per policy, visible
    # through the device status replacement_ordered.
    state[f"{_DELAYED_EMPLOYEE}_order"] = "delayed"
    return state


def _canned_response(callback_context: Any, llm_request: Any) -> LlmResponse:
    """Return a fixed model turn so no real model is ever reached."""
    del callback_context, llm_request
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text="ONBOARDING_READY")])
    )


def _provision_integration_world() -> None:
    """Provision the rows the /simulation/load contract cannot create.

    The harness plays world operator here (never the agents): five pending
    employees with identities, plus E103's already-assigned device and its
    in-flight order ORD-1 — the row the scenario's t=30 mutation delays.
    """
    with world_db.session_scope() as session:
        for index, employee_id in enumerate(_EMPLOYEES, start=1):
            session.add(
                Employee(
                    id=employee_id,
                    name=f"Integration Starter {index}",
                    role="Software Engineer",
                    location="Amsterdam",
                    manager_id=_MANAGER_ID,
                    start_date=_START_DATE,
                    status="pending",
                )
            )
            session.add(
                Identity(
                    employee_id=employee_id,
                    username=f"integration.starter{index}",
                    status="created",
                )
            )
        session.add(
            Device(
                id=f"DEV-{_DELAYED_EMPLOYEE}",
                employee_id=_DELAYED_EMPLOYEE,
                sku=_STANDARD_SKU,
                status="assigned",
            )
        )
        session.add(
            DeviceOrder(
                id="ORD-1",
                employee_id=_DELAYED_EMPLOYEE,
                sku=_STANDARD_SKU,
                status="ordered",
                eta="2026-09-04",
            )
        )
        session.commit()


async def _read_final_world_state() -> dict[str, Any]:
    """Summarise final world state through the agents' truthful read tools."""
    inventory = await device.check_inventory(_EMPLOYEES[0])
    state: dict[str, Any] = {
        "macbook_pro_14_available": inventory["available"][_STANDARD_SKU],
    }
    for employee_id in _EMPLOYEES:
        assignment = await device.get_device_assignment(employee_id)
        state[f"{employee_id}_device"] = (assignment.get("assigned_device") or {}).get(
            "status"
        )
        state[f"{employee_id}_order"] = (assignment.get("order") or {}).get(
            "status", "none"
        )
        requests = (await access.list_access_requests(employee_id)).get("requests", [])
        state[f"{employee_id}_grp_standard"] = next(
            (r["status"] for r in requests if r.get("group_id") == _STANDARD_GROUP),
            "none",
        )
        state[f"{employee_id}_grp_privileged"] = next(
            (r["status"] for r in requests if r.get("group_id") == _PRIVILEGED_GROUP),
            "none",
        )
    return state


class ScriptedIntegrationAgent:
    """Composite agent under test: REAL coordinator + scripted domain loops.

    ``run`` drives the whole integration flow: world provisioning, one case
    per employee, the real coordinator delegating both domains per case, and
    the scripted device/access loops reacting to the real delegation events.
    ``timeline_events`` records the snake_case trajectory events plus the
    canonical Event Store types observed on each case timeline;
    ``final_state`` / ``timeline_events`` are what the ScenarioEngine reads.
    """

    def __init__(
        self,
        scenario_id: str,
        backend: httpx.AsyncClient,
        coordinator: CoordinatorAgent,
    ) -> None:
        self.scenario_id = scenario_id
        self.backend = backend
        self.coordinator = coordinator
        self.timeline_events: list[str] = []
        # (employee_id, event) pairs for ordered, per-employee assertions.
        self.detail_events: list[tuple[str, str]] = []
        self.final_state: str | None = None
        self.verdicts: dict[str, dict[str, Any]] = {}

    def _record(self, event: str, employee_id: str | None = None) -> None:
        self.timeline_events.append(event)
        if employee_id is not None:
            self.detail_events.append((employee_id, event))

    async def run(self, user_message: str) -> str:
        """Entry point used by the ScenarioEngine."""
        del user_message
        drivers = {"integration-01-five-employees": self._drive_five_employees}
        driver = drivers.get(self.scenario_id)
        assert driver is not None, f"no scripted trajectory for {self.scenario_id}"
        await driver()
        return "done"

    # --- backend contract helpers --------------------------------------------

    async def _ack(self, workflow_id: str, agent_id: str) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/ack", headers={"X-Agent-Id": agent_id}
        )
        assert response.status_code == 200, response.text

    async def _report(self, workflow_id: str, agent_id: str, status: str) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/status",
            headers={"X-Agent-Id": agent_id},
            json={"workflow_id": workflow_id, "status": status, "blockers": []},
        )
        assert response.status_code == 200, response.text

    async def _complete_verified(
        self, workflow_id: str, agent_id: str, employee_id: str
    ) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/complete",
            headers={"X-Agent-Id": agent_id},
            json={"verified": True},
        )
        assert response.status_code == 200, response.text
        self._record("outcome_verified", employee_id)

    async def _case_events(self, case_id: str) -> list[dict[str, Any]]:
        response = await self.backend.get(f"/cases/{case_id}/events")
        assert response.status_code == 200, response.text
        events: list[dict[str, Any]] = response.json()["events"]
        return events

    # --- scenario driver -------------------------------------------------------

    async def _drive_five_employees(self) -> None:
        """Onboard E101..E105 with the real coordinator and both domains."""
        _provision_integration_world()
        for employee_id, case_id in _CASES.items():
            response = await self.backend.post(
                "/cases",
                json={"case_id": case_id, "employee_id": employee_id, "context": {}},
            )
            assert response.status_code == 201, response.text

        loops = [
            asyncio.create_task(self._device_loop()),
            asyncio.create_task(self._access_loop()),
        ]
        try:
            verdicts = await asyncio.gather(
                *(
                    self.coordinator.run_onboarding(
                        employee_id, case_id=case_id, workflows=list(_WORKFLOWS)
                    )
                    for employee_id, case_id in _CASES.items()
                )
            )
        finally:
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)

        for employee_id, verdict in zip(_CASES, verdicts, strict=True):
            assert verdict["verdict"] == "READY", verdict
            assert set(verdict["ready_goals"]) == _READY_GOALS
            assert verdict["missing_goals"] == []
            self._record("readiness_verdict_ready", employee_id)
            self.verdicts[employee_id] = verdict

        # Canonical Event Store observations (SPEC §23 types), per case.
        for case_id in _CASES.values():
            for event in await self._case_events(case_id):
                self.timeline_events.append(event["type"])
        self.final_state = "completed"

    # --- scripted device agent -------------------------------------------------

    async def _device_loop(self) -> None:
        """React to real device delegations across every case, in case order."""
        handled: set[str] = set()
        while True:
            for employee_id, case_id in _CASES.items():
                for event in await self._case_events(case_id):
                    if (
                        event["type"] == "WORKFLOW_DELEGATED"
                        and event["payload"].get("target_agent_id") == _DEVICE_AGENT
                        and event["workflow_id"] not in handled
                    ):
                        handled.add(event["workflow_id"])
                        await self._device_workflow(employee_id, event["workflow_id"])
            await asyncio.sleep(0.01)

    async def _device_workflow(self, employee_id: str, workflow_id: str) -> None:
        await self._ack(workflow_id, _DEVICE_AGENT)
        requirements = await device.get_employee_device_requirements(employee_id)
        assert "error" not in requirements, requirements
        assert requirements["location"] == "Amsterdam", requirements
        if employee_id == _DELAYED_EMPLOYEE:
            await self._device_delayed_order(employee_id, workflow_id)
            return
        inventory = await device.check_inventory(employee_id)
        self._record("inventory_checked", employee_id)
        assert inventory["available"][_STANDARD_SKU] >= 1, inventory
        result = await device.reserve_device(employee_id, _STANDARD_SKU)
        assert result["reserved"] is True, result
        self._record("device_reserved", employee_id)
        assignment = await device.get_device_assignment(employee_id)
        assert (assignment.get("assigned_device") or {}).get("status") == "assigned"
        self._record("delivery_verified", employee_id)
        await self._complete_verified(workflow_id, _DEVICE_AGENT, employee_id)

    async def _device_delayed_order(self, employee_id: str, workflow_id: str) -> None:
        """E103: detect the t=30 order delay via read tools, then replace."""
        assignment = await device.get_device_assignment(employee_id)
        assert (assignment.get("assigned_device") or {}).get("id") == "DEV-E103"
        assert (assignment.get("order") or {}).get("id") == "ORD-1", assignment
        for _ in range(300):  # 300 * 0.01s = 3s real-time budget
            assignment = await device.get_device_assignment(employee_id)
            if (assignment.get("order") or {}).get("status") == "delayed":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("timed order-delay mutation never landed")
        self._record("delivery_delay_detected", employee_id)
        replacement = await device.request_replacement(employee_id, "delivery delayed")
        assert "order" in replacement, replacement
        self._record("replacement_requested", employee_id)
        assignment = await device.get_device_assignment(employee_id)
        # Policy: replacement in flight — the device shows replacement_ordered.
        assert (assignment.get("assigned_device") or {}).get("status") == (
            "replacement_ordered"
        )
        await self._complete_verified(workflow_id, _DEVICE_AGENT, employee_id)

    # --- scripted access agent -------------------------------------------------

    async def _access_loop(self) -> None:
        """React to real access delegations; requests in strict employee order.

        Requests are gated on ALL five delegations and then created in
        employee order, so the world's REQ-n counter lines up deterministically
        with the scenario's t=60 grant mutations (REQ-1..REQ-6).
        """
        delegations = await self._await_access_delegations()
        for employee_id in _EMPLOYEES:
            await self._access_request_phase(employee_id, delegations[employee_id])

        pending = dict(delegations)
        for _ in range(600):  # 600 * 0.01s = 6s real-time budget
            if not pending:
                return
            for employee_id in list(pending):
                requests = (await access.list_access_requests(employee_id)).get(
                    "requests", []
                )
                if requests and all(r.get("status") == "granted" for r in requests):
                    self._record("access_granted", employee_id)
                    await self._complete_verified(
                        pending.pop(employee_id), _ACCESS_AGENT, employee_id
                    )
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed grant mutations never landed for {sorted(pending)}")

    async def _await_access_delegations(self) -> dict[str, str]:
        """Poll every case until all five access delegations have landed."""
        delegations: dict[str, str] = {}
        for _ in range(600):  # 600 * 0.01s = 6s real-time budget
            for employee_id, case_id in _CASES.items():
                if employee_id in delegations:
                    continue
                for event in await self._case_events(case_id):
                    if (
                        event["type"] == "WORKFLOW_DELEGATED"
                        and event["payload"].get("target_agent_id") == _ACCESS_AGENT
                    ):
                        delegations[employee_id] = event["workflow_id"]
                        break
            if len(delegations) == len(_EMPLOYEES):
                return delegations
            await asyncio.sleep(0.01)
        raise AssertionError("access delegations never landed for all employees")

    async def _access_request_phase(self, employee_id: str, workflow_id: str) -> None:
        await self._ack(workflow_id, _ACCESS_AGENT)
        summary = await access.get_access_summary(employee_id)
        assert summary.get("identity") is not None, summary
        # No entitlements yet: access is earned through the flow, not assumed.
        assert summary.get("entitlements") == [], summary
        await self._request_group(employee_id, _STANDARD_GROUP, "onboarding baseline")
        if employee_id == _PRIVILEGED_EMPLOYEE:
            await self._privileged_with_approval(employee_id, workflow_id)

    async def _request_group(
        self, employee_id: str, group_id: str, description: str
    ) -> None:
        result = await access.request_group_access(employee_id, group_id, description)
        assert result["requested"] is True, result
        self._record("access_requested", employee_id)
        self.detail_events.append((employee_id, f"access_requested:{group_id}"))

    async def _privileged_with_approval(
        self, employee_id: str, workflow_id: str
    ) -> None:
        """Privileged-group policy: manager approval BEFORE the world request."""
        task_id = f"HT-{employee_id}-privileged"
        response = await self.backend.post(
            "/tasks",
            json={
                "human_task_id": task_id,
                "case_id": _CASES[employee_id],
                "workflow_id": workflow_id,
                "requested_by": _ACCESS_AGENT,
                "requested_from": _MANAGER_ID,
                "type": "APPROVAL",
                "context": {
                    "reason": "privileged group requires manager approval",
                    "group_id": _PRIVILEGED_GROUP,
                    "employee_id": employee_id,
                },
                "allowed_actions": ["approve", "reject"],
                "status": "open",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        self._record("human_task_created", employee_id)
        await self._report(workflow_id, _ACCESS_AGENT, "waiting_for_human")
        # Scripted human behavior: the manager (requested_from, DEC-10) approves.
        decision = await self.backend.post(
            f"/tasks/{task_id}/decision",
            json={"decision": {"decision": "approve"}, "resolved_by": _MANAGER_ID},
        )
        assert decision.status_code == 200, decision.text
        self._record("approval_granted", employee_id)
        await self._request_group(employee_id, _PRIVILEGED_GROUP, "onboarding privileged")


@pytest.fixture
def world_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build MockWorld over a temp SQLite file shared with the backend."""
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "lab.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENTLAB_SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("ALLOWED_DOMAINS", "device-agent:devices,access-agent:access")
    monkeypatch.delenv("ALLOW_ANY_RESOLVER", raising=False)  # enforce DEC-10
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
def domain_transports(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> httpx.ASGITransport:
    """Route both domain agents' tool HTTP calls at the in-process MockWorld."""
    transport = httpx.ASGITransport(app=world_app)
    monkeypatch.setattr(device, "TRANSPORT", transport)
    monkeypatch.setattr(access, "TRANSPORT", transport)
    return transport


@pytest.fixture
async def backend_client(backend_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An async client for the in-process backend app."""
    transport = httpx.ASGITransport(app=backend_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://backend"
    ) as client:
        yield client


@pytest.fixture
def coordinator(backend_app: FastAPI) -> CoordinatorAgent:
    """The REAL coordinator against the in-process backend."""
    return CoordinatorAgent(
        backend_url="http://backend",
        transport=httpx.ASGITransport(app=backend_app),
    )


@pytest.mark.parametrize(
    "scenario_path",
    _SCENARIO_FILES,
    ids=[path.stem for path in _SCENARIO_FILES],
)
async def test_integration_scenario_passes(
    world_app: FastAPI,
    domain_transports: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    coordinator: CoordinatorAgent,
    monkeypatch: pytest.MonkeyPatch,
    scenario_path: Path,
) -> None:
    """Every integration scenario scores PASS against the SPEC §24 threshold."""
    del domain_transports  # monkeypatched; used implicitly by the tools
    scenario = load_scenario(scenario_path)
    monkeypatch.setattr(coordinator_module, "POLL_INTERVAL_SECONDS", 0.02)
    coordinator.agent.before_model_callback = _canned_response

    holder: dict[str, ScriptedIntegrationAgent] = {}

    def factory(
        fault_callbacks: tuple[Any, Any],
    ) -> tuple[ScriptedIntegrationAgent, asyncio.Event]:
        del fault_callbacks  # no tool faults in the integration batch-1 pack
        agent = ScriptedIntegrationAgent(scenario.id, backend_client, coordinator)
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
    agent = holder["agent"]
    final_state = await _read_final_world_state()
    # Multi-case run: the no-case-contamination assertion (single run_case_id)
    # does not apply, so case ids are intentionally not supplied.
    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state=final_state,
        expected_state=_expected_state(),
        retry_count=0,
        delegation_depth=1,  # coordinator -> domain agent
    )

    assert score.passed is True, f"{scenario.id} failed: {score.model_dump_json()}"
    assert score.total >= score.threshold
    assert result.final_state in scenario.expected.allowed_final_states
    for event in scenario.expected.forbidden_events:
        assert event not in agent.timeline_events

    # Multi-domain delegation: both domains, every employee, coordinator-actor.
    events_by_case = {
        case_id: (await backend_client.get(f"/cases/{case_id}/events")).json()["events"]
        for case_id in _CASES.values()
    }
    delegated = [
        event
        for events in events_by_case.values()
        for event in events
        if event["type"] == "WORKFLOW_DELEGATED"
    ]
    assert len(delegated) == 2 * len(_EMPLOYEES)
    assert all(event["actor"] == _COORDINATOR_ID for event in delegated)
    assert {(e["case_id"], e["payload"]["target_agent_id"]) for e in delegated} == {
        (case_id, agent_id)
        for case_id in _CASES.values()
        for agent_id in (_DEVICE_AGENT, _ACCESS_AGENT)
    }
    verified = [
        event
        for events in events_by_case.values()
        for event in events
        if event["type"] == "OUTCOME_VERIFIED"
    ]
    assert len(verified) == 2 * len(_EMPLOYEES)

    # HITL happened exactly once: E104's privileged-access approval by M1.
    for employee_id, case_id in _CASES.items():
        events = events_by_case[case_id]
        tasks_created = [e for e in events if e["type"] == "HUMAN_TASK_CREATED"]
        approvals = [e for e in events if e["type"] == "APPROVAL_GRANTED"]
        if employee_id == _PRIVILEGED_EMPLOYEE:
            assert len(tasks_created) == 1
            assert len(approvals) == 1
        else:
            assert tasks_created == []
            assert approvals == []

    # The privileged world request was created only AFTER the approval landed.
    approval_at = agent.detail_events.index((_PRIVILEGED_EMPLOYEE, "approval_granted"))
    request_at = agent.detail_events.index(
        (_PRIVILEGED_EMPLOYEE, f"access_requested:{_PRIVILEGED_GROUP}")
    )
    assert approval_at < request_at

    # Every employee's readiness verdict is READY with all outcomes verified.
    assert set(agent.verdicts) == set(_EMPLOYEES)
    for verdict in agent.verdicts.values():
        assert verdict["verdict"] == "READY"
        assert verdict["missing_goals"] == []

    # Final world state per employee (spot-checks beyond the scored diff).
    assert final_state["macbook_pro_14_available"] == 1
    assert final_state[f"{_DELAYED_EMPLOYEE}_device"] == "replacement_ordered"
    assert final_state[f"{_PRIVILEGED_EMPLOYEE}_grp_privileged"] == "granted"
