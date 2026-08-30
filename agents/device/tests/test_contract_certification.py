"""Contract certification suite for the Device agent (SPEC §19, A.13).

The gate for joining the final simulation. A :class:`RuntimeHarness` plays the
platform runtime role — accept → ack → run scripted agent → report — against
the REAL backend app and the REAL MockWorld app (both in-process ASGI over one
temp SQLite file). Scripted device behavior uses the canned
``before_model_callback`` pattern (no real LLM, no network) plus the real
MockWorld tools for truthful outcome verification.

Every SPEC §19 checklist item is an explicit harness step with its own
assertions; :data:`CONTRACT_CHECKLIST` lists the nine items and the final test
asserts each one is implemented and exercised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from google.adk.agents.context import Context
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from agentlab.backend import db as backend_db
from agentlab.backend.app import create_app as create_backend_app
from agentlab.world import db as world_db
from agentlab.world.app import create_app as create_world_app

from ..agent import build_device_agent
from ..tools import device

# The SPEC §19 contract certification checklist, one entry per required
# capability. Each entry maps to a ``RuntimeHarness.step_<item>`` method.
CONTRACT_CHECKLIST = [
    "accepts_workflow_request",
    "acknowledges_ownership",
    "reports_running",
    "reports_blockers",
    "creates_human_task",
    "resumes_after_resolution",
    "verifies_outcome",
    "reports_completed",
    "correlates_case_id",
]

_TOKEN = "test-token"
_EMPLOYEE_ID = "E42"
_MANAGER_ID = "M1"
_UNAUTHORIZED_ACTOR = "unknown-actor"  # placeholder; never requested_from (DEC-10)
_COORDINATOR_ID = "onboarding-coordinator"


def _canned_response(callback_context: Context, llm_request: LlmRequest) -> LlmResponse:
    """Return a fixed model turn so the scripted agent never reaches a model."""
    del callback_context, llm_request  # the canned turn ignores both
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text="DEVICE_READY")]),
        turn_complete=True,
    )


class RecordingTransport(httpx.AsyncBaseTransport):
    """ASGI transport that records every request the device tools make."""

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
    """Plays the Agent Lab runtime for one device-agent workflow (SPEC §19).

    Each ``step_*`` method certifies one checklist item against the real
    backend and records it in :attr:`covered`.
    """

    def __init__(self, backend: httpx.AsyncClient, world_log: RecordingTransport) -> None:
        self.backend = backend
        self.world_log = world_log
        self.agent_id = "device-agent"
        self.case_id = "ONB-E42-CONTRACT"
        self.employee_id = _EMPLOYEE_ID
        self.workflow_id = "WF-D-CONTRACT-1"
        self.task_id = "HT-D-CONTRACT-1"
        self.goal = "employee_device_ready"
        self.covered: set[str] = set()
        self.verified_outcome = False

    async def certify(self) -> None:
        """Run the full SPEC §19 certification flow in contract order."""
        await self.step_accepts_workflow_request()
        await self.step_acknowledges_ownership()
        await self.run_scripted_agent_turn()
        await self.step_reports_running()
        await self.step_reports_blockers()
        await self.step_creates_human_task()
        await self.step_resumes_after_resolution()
        await self.step_verifies_outcome()
        await self.step_reports_completed()
        await self.step_correlates_case_id()

    async def run_scripted_agent_turn(self) -> None:
        """Run the scripted device agent (canned model callback, no real LLM)."""
        agent = build_device_agent()
        agent.agent.before_model_callback = _canned_response
        text = await agent.arun("Make sure E42 has a device before their start date.")
        assert "DEVICE_READY" in text

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

    async def step_reports_blockers(self) -> None:
        """§19: BLOCKED persists blockers, emits BLOCKER_CREATED, shows on case."""
        blocker = {"code": "NO_INVENTORY", "description": "Standard device unavailable"}
        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/status",
            json={
                "workflow_id": self.workflow_id,
                "status": "blocked",
                "blockers": [blocker],
            },
            headers=self._agent_headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "blocked"
        assert response.json()["blockers"] == [blocker]

        events = await self.backend.get(f"/cases/{self.case_id}/events")
        assert any(
            event["type"] == "BLOCKER_CREATED"
            and event["payload"]["code"] == "NO_INVENTORY"
            for event in events.json()["events"]
        ), events.json()

        case = await self.backend.get(f"/cases/{self.case_id}")
        assert case.json()["domain_status"]["device"] == "blocked"

        # Unblock (BLOCKED → RUNNING) so the certification flow can continue.
        resumed = await self.backend.post(
            f"/workflows/{self.workflow_id}/status",
            json={
                "workflow_id": self.workflow_id,
                "status": "running",
                "blockers": [],
            },
            headers=self._agent_headers(),
        )
        assert resumed.status_code == 200, resumed.text
        self.covered.add("reports_blockers")

    async def step_creates_human_task(self) -> None:
        """§19: the agent opens a persisted HumanTask (substitution approval)."""
        response = await self.backend.post(
            "/tasks",
            json={
                "human_task_id": self.task_id,
                "case_id": self.case_id,
                "workflow_id": self.workflow_id,
                "requested_by": self.agent_id,
                "requested_from": _MANAGER_ID,
                "type": "APPROVAL",
                "context": {"reason": "substitute macbook_air_15"},
                "allowed_actions": ["approve", "reject"],
                "status": "open",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["case_id"] == self.case_id
        assert body["workflow_id"] == self.workflow_id
        assert body["status"] == "open"

        fetched = await self.backend.get(f"/tasks/{self.task_id}")
        assert fetched.status_code == 200
        assert fetched.json()["case_id"] == self.case_id
        assert fetched.json()["workflow_id"] == self.workflow_id

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
            json={"decision": {"decision": "approve"}, "resolved_by": _MANAGER_ID},
        )
        assert granted.status_code == 200, granted.text
        assert granted.json()["status"] == "resolved"
        assert granted.json()["resolved_by"] == _MANAGER_ID

        case = await self.backend.get(f"/cases/{self.case_id}")
        assert case.json()["domain_status"]["device"] == "running"

        events = await self.backend.get(f"/cases/{self.case_id}/events")
        assert any(
            event["type"] == "APPROVAL_GRANTED" for event in events.json()["events"]
        )
        self.covered.add("resumes_after_resolution")

    async def step_verifies_outcome(self) -> None:
        """§19: outcome is verified via the agent's truthful read tool."""
        reservation = await device.reserve_device(self.employee_id, "macbook_pro_14")
        assert reservation["reserved"] is True, reservation

        assignment = await device.get_device_assignment(self.employee_id)
        assert assignment["assigned_device"] is not None
        assert assignment["assigned_device"]["sku"] == "macbook_pro_14"
        self.verified_outcome = True

        # The read tool really ran against MockWorld (tracked at the transport).
        assert self.world_log.saw("POST", "/world/devices/E42/reserve")
        assert self.world_log.saw("GET", "/world/devices/E42")
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
        assert case.json()["domain_status"]["device"] == "completed"

        # A different case sees none of this run's events (DEC-06 isolation).
        other = await self.backend.get("/cases/ONB-OTHER/events")
        assert other.json()["events"] == []
        self.covered.add("correlates_case_id")


@pytest.fixture
def world_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build MockWorld + the backend over one temp shared SQLite file."""
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "lab.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENTLAB_SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("ALLOWED_DOMAINS", "device-agent:devices")
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
def world_log(world_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> RecordingTransport:
    """Route the device tools at MockWorld through a recording transport."""
    recorder = RecordingTransport(world_app)
    monkeypatch.setattr(device, "TRANSPORT", recorder)
    return recorder


@pytest.fixture
async def backend_client(backend_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An async client for the in-process backend app."""
    transport = httpx.ASGITransport(app=backend_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://backend"
    ) as client:
        yield client


async def test_contract_certification_full_flow(
    world_app: FastAPI,
    world_log: RecordingTransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """The device agent demonstrates every SPEC §19 contract capability."""
    del world_app  # kept for fixture ordering; MockWorld is hit via world_log
    harness = RuntimeHarness(backend_client, world_log)
    await harness.certify()

    assert harness.verified_outcome is True
    assert harness.covered == set(CONTRACT_CHECKLIST)


async def test_contract_checklist_is_fully_covered(
    world_app: FastAPI,
    world_log: RecordingTransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """Each §19 checklist item has a harness step and is exercised by it."""
    del world_app
    assert len(CONTRACT_CHECKLIST) == 9
    for item in CONTRACT_CHECKLIST:
        step = getattr(RuntimeHarness, f"step_{item}", None)
        assert callable(step), f"missing RuntimeHarness.step_{item}"

    harness = RuntimeHarness(backend_client, world_log)
    await harness.certify()
    missing = [item for item in CONTRACT_CHECKLIST if item not in harness.covered]
    assert missing == [], f"uncovered §19 items: {missing}"
