"""Contract certification suite for the Systems agent (SPEC §19, slim).

A :class:`RuntimeHarness` plays the platform runtime role — accept → ack →
run → report — against the REAL backend app and the REAL MockWorld app (both
in-process ASGI over one temp SQLite file), exercising the systems domain's
HITL shape: provisioning is an IT HumanTask opened through the real backend
task flow by the ``provision_account`` tool, because the systems world
surface is read-only. Outcome verification uses the real
``get_account_status`` / ``verify_account`` tools, so completion is grounded
in world state.

:data:`CONTRACT_CHECKLIST` lists the covered §19 capabilities and the final
test asserts each one is implemented and exercised.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agentlab.world import db as world_db
from agentlab.world.models import SystemAccount

from ..tools import systems

# The SPEC §19 capabilities this slim certification covers, one entry per
# capability. Each entry maps to a ``RuntimeHarness.step_<item>`` method.
CONTRACT_CHECKLIST = [
    "accepts_workflow_request",
    "acknowledges_ownership",
    "reports_running",
    "creates_human_task",
    "resumes_after_resolution",
    "verifies_outcome",
    "reports_completed",
    "correlates_case_id",
]

_EMPLOYEE_ID = "E42"
_IT_ACTOR = "it-support"  # the provisioning queue; only it may resolve (DEC-10)
_UNAUTHORIZED_ACTOR = "unknown-actor"  # placeholder; never requested_from (DEC-10)
_COORDINATOR_ID = "onboarding-coordinator"


class RecordingTransport(httpx.AsyncBaseTransport):
    """ASGI transport that records every request the systems tools make."""

    def __init__(self, app: FastAPI) -> None:
        self._transport = httpx.ASGITransport(app=app)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._transport.handle_async_request(request)

    def saw(self, method: str, path: str) -> bool:
        """True when a recorded request matches ``method`` + ``path``."""
        return any(
            request.method == method and request.url.path == path
            for request in self.requests
        )


class RuntimeHarness:
    """Plays the Agent Lab runtime for one systems-agent workflow (SPEC §19).

    Each ``step_*`` method certifies one checklist item against the real
    backend and records it in :attr:`covered`.
    """

    def __init__(self, backend: httpx.AsyncClient, world_log: RecordingTransport) -> None:
        self.backend = backend
        self.world_log = world_log
        self.agent_id = "systems-agent"
        self.case_id = "ONB-E42-SYSTEMS-CONTRACT"
        self.employee_id = _EMPLOYEE_ID
        self.workflow_id = "WF-S-CONTRACT-1"
        self.goal = "employee_systems_ready"
        self.task_id = ""  # assigned when the provisioning task is opened
        self.covered: set[str] = set()
        self.verified_outcome = False

    async def certify(self) -> None:
        """Run the certification flow in contract order."""
        await self.step_accepts_workflow_request()
        await self.step_acknowledges_ownership()
        await self.step_reports_running()
        await self.step_creates_human_task()
        await self.step_resumes_after_resolution()
        await self.step_verifies_outcome()
        await self.step_reports_completed()
        await self.step_correlates_case_id()

    def _agent_headers(self, agent_id: str | None = None) -> dict[str, str]:
        return {"X-Agent-Id": agent_id or self.agent_id}

    async def step_accepts_workflow_request(self) -> None:
        """§19: the runtime delegates a WorkflowRequest; the row is created."""
        response = await self.backend.post(
            "/cases",
            json={
                "case_id": self.case_id,
                "employee_id": self.employee_id,
                "context": {"source": "contract-certification"},
            },
        )
        assert response.status_code == 201, response.text

        response = await self.backend.post(
            "/workflows",
            json={
                "workflow_id": self.workflow_id,
                "case_id": self.case_id,
                "goal": self.goal,
                "employee_id": self.employee_id,
                "context": {},
                "target_agent_id": self.agent_id,
            },
            headers=self._agent_headers(_COORDINATOR_ID),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["workflow_id"] == self.workflow_id
        assert body["case_id"] == self.case_id
        assert body["agent_id"] == self.agent_id
        assert body["status"] == "acknowledged"
        self.covered.add("accepts_workflow_request")

    async def step_acknowledges_ownership(self) -> None:
        """§19: only the owning agent may ack; ACKNOWLEDGED → RUNNING."""
        wrong = await self.backend.post(
            f"/workflows/{self.workflow_id}/ack",
            headers=self._agent_headers("access-agent"),
        )
        assert wrong.status_code == 403, wrong.text

        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/ack",
            headers=self._agent_headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "running"
        self.covered.add("acknowledges_ownership")

    async def step_reports_running(self) -> None:
        """§19: the agent reports a WorkflowStatus of RUNNING."""
        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/status",
            json={
                "workflow_id": self.workflow_id,
                "status": "running",
                "blockers": [],
            },
            headers=self._agent_headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "running"
        self.covered.add("reports_running")

    async def step_creates_human_task(self) -> None:
        """§19: the provisioning tool opens a persisted HumanTask (IT ticket).

        The systems world surface is read-only — there is no provisioning
        route — so ``provision_account`` goes through the real backend task
        flow and returns the task reference, never a fake success.
        """
        world_calls_before = len(self.world_log.requests)
        result = await systems.provision_account(
            self.employee_id,
            "SYS-VPN",
            case_id=self.case_id,
            workflow_id=self.workflow_id,
        )
        assert result["task_opened"] is True, result
        task = result["task"]
        self.task_id = task["human_task_id"]
        assert task["case_id"] == self.case_id
        assert task["workflow_id"] == self.workflow_id
        assert task["requested_by"] == self.agent_id
        assert task["requested_from"] == _IT_ACTOR
        assert task["type"] == "MANUAL_ACTION"
        assert task["status"] == "open"

        # The task row is persisted and correlated with the case (SPEC §19).
        fetched = await self.backend.get(f"/tasks/{self.task_id}")
        assert fetched.status_code == 200
        assert fetched.json()["case_id"] == self.case_id
        assert fetched.json()["workflow_id"] == self.workflow_id

        # Provisioning never touched the read-only world surface.
        assert len(self.world_log.requests) == world_calls_before

        waiting = await self.backend.post(
            f"/workflows/{self.workflow_id}/status",
            json={
                "workflow_id": self.workflow_id,
                "status": "waiting_for_human",
                "blockers": [],
            },
            headers=self._agent_headers(),
        )
        assert waiting.status_code == 200, waiting.text
        self.covered.add("creates_human_task")

    async def step_resumes_after_resolution(self) -> None:
        """§19: decision → event → resume (WAITING_FOR_HUMAN → RUNNING)."""
        # DEC-10: an unauthorized resolver is rejected with 403.
        unauthorized = await self.backend.post(
            f"/tasks/{self.task_id}/decision",
            json={"decision": {"decision": "approve"}, "resolved_by": _UNAUTHORIZED_ACTOR},
        )
        assert unauthorized.status_code == 403, unauthorized.text

        granted = await self.backend.post(
            f"/tasks/{self.task_id}/decision",
            json={"decision": {"decision": "approve"}, "resolved_by": _IT_ACTOR},
        )
        assert granted.status_code == 200, granted.text
        assert granted.json()["status"] == "resolved"
        assert granted.json()["resolved_by"] == _IT_ACTOR

        case = await self.backend.get(f"/cases/{self.case_id}")
        assert case.json()["domain_status"]["systems"] == "running"

        events = await self.backend.get(f"/cases/{self.case_id}/events")
        assert any(
            event["type"] == "APPROVAL_GRANTED" for event in events.json()["events"]
        )
        self.covered.add("resumes_after_resolution")

    async def step_verifies_outcome(self) -> None:
        """§19: outcome is verified via the agent's truthful read tools."""
        # The world operator (IT) materializes the provisioned accounts; the
        # agent can never create rows — the systems surface is read-only.
        with world_db.session_scope() as session:
            session.add(
                SystemAccount(
                    id="SYSACC-E42-EMAIL",
                    employee_id=self.employee_id,
                    system_id="SYS-EMAIL",
                    status="active",
                )
            )
            session.add(
                SystemAccount(
                    id="SYSACC-E42-VPN",
                    employee_id=self.employee_id,
                    system_id="SYS-VPN",
                    status="active",
                )
            )
            session.commit()

        verification = await systems.verify_account(self.employee_id)
        assert verification["all_required_verified"] is True, verification
        assert verification["verified"]["SYS-EMAIL"] is True
        assert verification["verified"]["SYS-VPN"] is True
        assert verification["accounts"]["SYS-HR"] == "missing"  # non-manager
        assert verification["policy_violations"] == []
        self.verified_outcome = True

        # The read tools really ran against MockWorld (tracked at transport),
        # and nothing ever POSTed to the read-only systems surface.
        assert self.world_log.saw("GET", "/world/systems/E42")
        assert self.world_log.saw("GET", "/world/employees/E42")
        assert all(
            request.method == "GET" for request in self.world_log.requests
        )
        self.covered.add("verifies_outcome")

    async def step_reports_completed(self) -> None:
        """§19: COMPLETED requires verified=true; verified=false → 409."""
        unverified = await self.backend.post(
            f"/workflows/{self.workflow_id}/complete",
            json={"verified": False},
            headers=self._agent_headers(),
        )
        assert unverified.status_code == 409, unverified.text

        assert self.verified_outcome is True  # completion follows verification
        completed = await self.backend.post(
            f"/workflows/{self.workflow_id}/complete",
            json={"verified": True},
            headers=self._agent_headers(),
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert completed.json()["verified"] is True
        self.covered.add("reports_completed")

    async def step_correlates_case_id(self) -> None:
        """§19: every event, task, and workflow shares the run's case_id."""
        events = await self.backend.get(f"/cases/{self.case_id}/events")
        timeline: list[dict[str, Any]] = events.json()["events"]
        assert timeline, "expected a non-empty case timeline"
        for event in timeline:
            assert event["case_id"] == self.case_id
            assert event["workflow_id"] in (None, self.workflow_id)

        tasks = await self.backend.get("/tasks", params={"case_id": self.case_id})
        task_rows: list[dict[str, Any]] = tasks.json()
        assert task_rows, "expected at least the certification task"
        for task in task_rows:
            assert task["case_id"] == self.case_id
            assert task["workflow_id"] == self.workflow_id

        case = await self.backend.get(f"/cases/{self.case_id}")
        assert case.json()["case_id"] == self.case_id
        assert case.json()["domain_status"]["systems"] == "completed"

        # A different case sees none of this run's events (DEC-06 isolation).
        other = await self.backend.get("/cases/ONB-OTHER/events")
        assert other.json()["events"] == []
        self.covered.add("correlates_case_id")


@pytest.fixture
def world_log(world_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> RecordingTransport:
    """Route the systems tools at MockWorld through a recording transport."""
    recorder = RecordingTransport(world_app)
    monkeypatch.setattr(systems, "TRANSPORT", recorder)
    return recorder


@pytest.fixture
def backend_log(
    backend_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> httpx.ASGITransport:
    """Route the provisioning tool's backend task-flow calls in-process."""
    transport = httpx.ASGITransport(app=backend_app)
    monkeypatch.setattr(systems, "BACKEND_TRANSPORT", transport)
    return transport


async def test_contract_certification_full_flow(
    world_app: FastAPI,
    world_log: RecordingTransport,
    backend_log: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """The systems agent demonstrates every covered SPEC §19 capability."""
    del world_app, backend_log  # fixture ordering; MockWorld is hit via world_log
    harness = RuntimeHarness(backend_client, world_log)
    await harness.certify()

    assert harness.verified_outcome is True
    assert harness.covered == set(CONTRACT_CHECKLIST)


async def test_contract_checklist_is_fully_covered(
    world_app: FastAPI,
    world_log: RecordingTransport,
    backend_log: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """Each checklist item has a harness step and is exercised by it."""
    del world_app, backend_log
    for item in CONTRACT_CHECKLIST:
        step = getattr(RuntimeHarness, f"step_{item}", None)
        assert callable(step), f"missing RuntimeHarness.step_{item}"

    harness = RuntimeHarness(backend_client, world_log)
    await harness.certify()
    missing = [item for item in CONTRACT_CHECKLIST if item not in harness.covered]
    assert missing == [], f"uncovered §19 items: {missing}"
