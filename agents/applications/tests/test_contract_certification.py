"""Contract certification suite for the Applications agent (SPEC §19, slim).

A :class:`RuntimeHarness` plays the platform runtime role — accept → ack →
run → report — against the REAL backend app and the REAL MockWorld app (both
in-process ASGI over one temp SQLite file), exercising the applications
domain's full-mutator shape: provisioning is a real world mutation performed
by the ``provision_application`` tool through the idempotent grant route,
and outcome verification uses the real ``get_application_access`` /
``verify_application_access`` tools, so completion is grounded in world
state.

:data:`CONTRACT_CHECKLIST` lists the covered §19 capabilities and the final
test asserts each one is implemented and exercised.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ..tools import applications

# The SPEC §19 capabilities this slim certification covers, one entry per
# capability. Each entry maps to a ``RuntimeHarness.step_<item>`` method.
CONTRACT_CHECKLIST = [
    "accepts_workflow_request",
    "acknowledges_ownership",
    "reports_running",
    "provisions_application",
    "verifies_outcome",
    "reports_completed",
    "correlates_case_id",
]

_EMPLOYEE_ID = "E42"
_COORDINATOR_ID = "onboarding-coordinator"


class RecordingTransport(httpx.AsyncBaseTransport):
    """ASGI transport that records every request the applications tools make."""

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
    """Plays the Agent Lab runtime for one applications-agent workflow (SPEC §19).

    Each ``step_*`` method certifies one checklist item against the real
    backend and records it in :attr:`covered`.
    """

    def __init__(self, backend: httpx.AsyncClient, world_log: RecordingTransport) -> None:
        self.backend = backend
        self.world_log = world_log
        self.agent_id = "applications-agent"
        self.case_id = "ONB-E42-APPLICATIONS-CONTRACT"
        self.employee_id = _EMPLOYEE_ID
        self.workflow_id = "WF-A-CONTRACT-1"
        self.goal = "employee_applications_ready"
        self.covered: set[str] = set()
        self.verified_outcome = False

    async def certify(self) -> None:
        """Run the certification flow in contract order."""
        await self.step_accepts_workflow_request()
        await self.step_acknowledges_ownership()
        await self.step_reports_running()
        await self.step_provisions_application()
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

    async def step_provisions_application(self) -> None:
        """§19: provisioning is a REAL world mutation via the real tool.

        Applications is a full mutator domain: ``provision_application``
        calls the idempotent grant route and the grant is immediately visible
        to truthful reads. The seed already grants APP-SLACK and
        APP-GOOGLE-WORKSPACE; APP-GITHUB (required for E42's engineering
        role) is missing and gets provisioned here.
        """
        before = await applications.get_application_access(self.employee_id)
        grants = {row["application_id"]: row["granted"] for row in before["applications"]}
        assert grants["APP-SLACK"] is True
        assert grants["APP-GOOGLE-WORKSPACE"] is True
        assert grants["APP-GITHUB"] is False

        result = await applications.provision_application(self.employee_id, "APP-GITHUB")
        assert result["provisioned"] is True, result
        access = result["application_access"]
        assert access["employee_id"] == self.employee_id
        assert access["application_id"] == "APP-GITHUB"
        assert access["status"] == "granted"

        # The mutation really went through the world route.
        assert self.world_log.saw("POST", "/world/applications/E42/provision")
        self.covered.add("provisions_application")

    async def step_verifies_outcome(self) -> None:
        """§19: outcome is verified via the agent's truthful read tools."""
        verification = await applications.verify_application_access(self.employee_id)
        assert verification["all_required_verified"] is True, verification
        assert verification["verified"]["APP-SLACK"] is True
        assert verification["verified"]["APP-GOOGLE-WORKSPACE"] is True
        assert verification["verified"]["APP-GITHUB"] is True
        assert verification["missing_required"] == []
        assert verification["policy_violations"] == []
        self.verified_outcome = True

        # The read tools really ran against MockWorld (tracked at transport).
        assert self.world_log.saw("GET", "/world/applications/E42")
        assert self.world_log.saw("GET", "/world/employees/E42")
        # The tools never touch the privileged simulation surface.
        assert all(
            not request.url.path.startswith("/simulation/")
            for request in self.world_log.requests
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
        """§19: every event and workflow shares the run's case_id."""
        events = await self.backend.get(f"/cases/{self.case_id}/events")
        timeline: list[dict[str, Any]] = events.json()["events"]
        assert timeline, "expected a non-empty case timeline"
        for event in timeline:
            assert event["case_id"] == self.case_id
            assert event["workflow_id"] in (None, self.workflow_id)

        case = await self.backend.get(f"/cases/{self.case_id}")
        assert case.json()["case_id"] == self.case_id
        assert case.json()["domain_status"]["applications"] == "completed"

        # A different case sees none of this run's events (DEC-06 isolation).
        other = await self.backend.get("/cases/ONB-OTHER/events")
        assert other.json()["events"] == []
        self.covered.add("correlates_case_id")


@pytest.fixture
def world_log(world_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> RecordingTransport:
    """Route the applications tools at MockWorld through a recording transport."""
    recorder = RecordingTransport(world_app)
    monkeypatch.setattr(applications, "TRANSPORT", recorder)
    return recorder


async def test_contract_certification_full_flow(
    world_app: FastAPI,
    world_log: RecordingTransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """The applications agent demonstrates every covered SPEC §19 capability."""
    del world_app  # fixture ordering; MockWorld is hit via world_log
    harness = RuntimeHarness(backend_client, world_log)
    await harness.certify()

    assert harness.verified_outcome is True
    assert harness.covered == set(CONTRACT_CHECKLIST)


async def test_contract_checklist_is_fully_covered(
    world_app: FastAPI,
    world_log: RecordingTransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """Each checklist item has a harness step and is exercised by it."""
    del world_app
    for item in CONTRACT_CHECKLIST:
        step = getattr(RuntimeHarness, f"step_{item}", None)
        assert callable(step), f"missing RuntimeHarness.step_{item}"

    harness = RuntimeHarness(backend_client, world_log)
    await harness.certify()
    missing = [item for item in CONTRACT_CHECKLIST if item not in harness.covered]
    assert missing == [], f"uncovered §19 items: {missing}"
