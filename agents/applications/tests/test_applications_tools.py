"""Tests for the Applications agent's tools against the REAL world app.

Every tool is exercised over in-process ASGI against the real
``/world/applications`` and shared ``/world/employees`` routes with the
canonical seed (E42 Software Engineer; APP-SLACK + APP-GOOGLE-WORKSPACE
granted, APP-GITHUB not granted). Assertions cover the happy path, the
unknown-application 404 envelope, idempotent double-provisioning, the
out-of-role policy violation, the ``X-Agent-Id`` header, and the never-raise
contract on transport failure.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from agentlab.world import db as world_db
from agentlab.world.models import Employee

from ..tools import applications

_EMPLOYEE_ID = "E42"


class RecordingTransport(httpx.AsyncBaseTransport):
    """ASGI transport that records every request the applications tools make."""

    def __init__(self, app: FastAPI) -> None:
        self._transport = httpx.ASGITransport(app=app)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._transport.handle_async_request(request)


@pytest.fixture
def recording_transport(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> RecordingTransport:
    """Route the applications tools at MockWorld through a recording transport."""
    recorder = RecordingTransport(world_app)
    monkeypatch.setattr(applications, "TRANSPORT", recorder)
    return recorder


def _set_role(employee_id: str, role: str) -> None:
    """World-operator role change (the agent can never edit employees)."""
    with world_db.session_scope() as session:
        employee = session.get(Employee, employee_id)
        assert employee is not None
        employee.role = role
        session.add(employee)
        session.commit()


async def test_get_required_applications(recording_transport: RecordingTransport) -> None:
    result = await applications.get_required_applications(_EMPLOYEE_ID)

    assert result["employee_id"] == _EMPLOYEE_ID
    assert result["role"] == "Software Engineer"
    assert result["manager_id"] == "M1"
    assert result["required_applications"] == [
        "APP-SLACK",
        "APP-GOOGLE-WORKSPACE",
        "APP-GITHUB",
    ]
    assert result["github_required"] is True


async def test_get_required_applications_non_engineering_role(
    recording_transport: RecordingTransport,
) -> None:
    """A non-engineering role requires the baseline only — never APP-GITHUB."""
    _set_role(_EMPLOYEE_ID, "Marketing Specialist")

    result = await applications.get_required_applications(_EMPLOYEE_ID)

    assert result["role"] == "Marketing Specialist"
    assert result["required_applications"] == ["APP-SLACK", "APP-GOOGLE-WORKSPACE"]
    assert result["github_required"] is False


async def test_get_required_applications_unknown_employee(
    recording_transport: RecordingTransport,
) -> None:
    """An employee unknown to the world surfaces the NOT_FOUND envelope."""
    result = await applications.get_required_applications("E404")

    assert result["error"]["code"] == "NOT_FOUND"


async def test_get_application_access_seed(recording_transport: RecordingTransport) -> None:
    """Seed state: Slack + Google Workspace granted, GitHub not granted."""
    result = await applications.get_application_access(_EMPLOYEE_ID)

    assert result["employee_id"] == _EMPLOYEE_ID
    assert [
        (row["application_id"], row["granted"]) for row in result["applications"]
    ] == [
        ("APP-GITHUB", False),
        ("APP-GOOGLE-WORKSPACE", True),
        ("APP-SLACK", True),
    ]


async def test_provision_application_grants(recording_transport: RecordingTransport) -> None:
    result = await applications.provision_application(_EMPLOYEE_ID, "APP-GITHUB")

    assert result["provisioned"] is True
    access = result["application_access"]
    assert access["employee_id"] == _EMPLOYEE_ID
    assert access["application_id"] == "APP-GITHUB"
    assert access["status"] == "granted"

    # The grant really landed in the world (truthful read).
    after = await applications.get_application_access(_EMPLOYEE_ID)
    grants = {row["application_id"]: row["granted"] for row in after["applications"]}
    assert grants["APP-GITHUB"] is True


async def test_provision_application_is_idempotent(
    recording_transport: RecordingTransport,
) -> None:
    """Re-granting an already-granted application returns granted, not an error."""
    first = await applications.provision_application(_EMPLOYEE_ID, "APP-SLACK")
    second = await applications.provision_application(_EMPLOYEE_ID, "APP-SLACK")

    assert first["provisioned"] is True
    assert second["provisioned"] is True
    assert first["application_access"]["status"] == "granted"
    assert second["application_access"]["status"] == "granted"
    # Same row re-marked, not a duplicate.
    assert first["application_access"]["id"] == second["application_access"]["id"]


async def test_provision_application_unknown_id_surfaces_404(
    recording_transport: RecordingTransport,
) -> None:
    """An application absent from the catalog surfaces the NOT_FOUND envelope."""
    result = await applications.provision_application(_EMPLOYEE_ID, "APP-UNKNOWN")

    assert result["provisioned"] is False
    assert result["code"] == "NOT_FOUND"
    assert "APP-UNKNOWN" in result["description"]

    # The world is untouched: nothing was granted.
    after = await applications.get_application_access(_EMPLOYEE_ID)
    grants = {row["application_id"]: row["granted"] for row in after["applications"]}
    assert grants == {
        "APP-GITHUB": False,
        "APP-GOOGLE-WORKSPACE": True,
        "APP-SLACK": True,
    }


async def test_verify_application_access_missing_required(
    recording_transport: RecordingTransport,
) -> None:
    """Seed state: GitHub is required (engineer) and missing — never verified."""
    result = await applications.verify_application_access(_EMPLOYEE_ID)

    assert result["all_required_verified"] is False
    assert result["verified"]["APP-SLACK"] is True
    assert result["verified"]["APP-GOOGLE-WORKSPACE"] is True
    assert result["verified"]["APP-GITHUB"] is False  # absent grant: never verified
    assert result["missing_required"] == ["APP-GITHUB"]
    assert result["policy_violations"] == []


async def test_verify_application_access_all_granted(
    recording_transport: RecordingTransport,
) -> None:
    await applications.provision_application(_EMPLOYEE_ID, "APP-GITHUB")

    result = await applications.verify_application_access(_EMPLOYEE_ID)

    assert result["all_required_verified"] is True
    assert result["missing_required"] == []
    assert result["policy_violations"] == []
    assert all(result["verified"][app] is True for app in result["required_applications"])


async def test_verify_flags_out_of_role_github_grant(
    recording_transport: RecordingTransport,
) -> None:
    """A GitHub grant for a non-engineer is a violation, never a verification."""
    await applications.provision_application(_EMPLOYEE_ID, "APP-GITHUB")
    _set_role(_EMPLOYEE_ID, "Marketing Specialist")

    result = await applications.verify_application_access(_EMPLOYEE_ID)

    assert result["policy_violations"] == ["out_of_role_application_granted"]
    assert result["verified"]["APP-GITHUB"] is False
    # The baseline is still fully verified; the violation is the GitHub grant.
    assert result["all_required_verified"] is True
    assert result["missing_required"] == []


async def test_sends_x_agent_id_header(recording_transport: RecordingTransport) -> None:
    await applications.get_required_applications(_EMPLOYEE_ID)
    await applications.get_application_access(_EMPLOYEE_ID)
    await applications.provision_application(_EMPLOYEE_ID, "APP-GITHUB")
    await applications.verify_application_access(_EMPLOYEE_ID)

    assert recording_transport.requests
    for request in recording_transport.requests:
        assert request.headers.get("x-agent-id") == "applications-agent"


async def test_tools_only_touch_applications_and_shared_routes(
    recording_transport: RecordingTransport,
) -> None:
    await applications.get_required_applications(_EMPLOYEE_ID)
    await applications.get_application_access(_EMPLOYEE_ID)
    await applications.provision_application(_EMPLOYEE_ID, "APP-GITHUB")
    await applications.verify_application_access(_EMPLOYEE_ID)

    paths = {request.url.path for request in recording_transport.requests}
    assert all(
        path.startswith("/world/applications/") or path.startswith("/world/employees/")
        for path in paths
    )
    assert not any(path.startswith("/simulation/") for path in paths)


async def test_domain_enforcement_error_envelope(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the applications domain grant, the world rejects with FORBIDDEN."""
    monkeypatch.setenv("ALLOWED_DOMAINS", "access-agent:access")
    monkeypatch.setattr(applications, "TRANSPORT", httpx.ASGITransport(app=world_app))

    access = await applications.get_application_access(_EMPLOYEE_ID)
    assert access["applications"] == []
    assert access["error"] == {
        "code": "FORBIDDEN",
        "description": "Agent 'applications-agent' is not permitted in domain 'applications'",
    }

    # The shared employees route requires a REGISTERED agent (403 as well).
    required = await applications.get_required_applications(_EMPLOYEE_ID)
    assert required["error"]["code"] == "FORBIDDEN"

    provisioned = await applications.provision_application(_EMPLOYEE_ID, "APP-GITHUB")
    assert provisioned["provisioned"] is False
    assert provisioned["code"] == "FORBIDDEN"


async def test_tools_never_raise_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(applications, "TRANSPORT", httpx.MockTransport(handler))

    required = await applications.get_required_applications(_EMPLOYEE_ID)
    assert required["error"]["code"] == "NETWORK_ERROR"

    access = await applications.get_application_access(_EMPLOYEE_ID)
    assert access["applications"] == []
    assert access["error"]["code"] == "NETWORK_ERROR"

    result = await applications.provision_application(_EMPLOYEE_ID, "APP-GITHUB")
    assert result["provisioned"] is False
    assert result["code"] == "NETWORK_ERROR"

    verified = await applications.verify_application_access(_EMPLOYEE_ID)
    assert verified["all_required_verified"] is False
    assert verified["error"]["code"] == "NETWORK_ERROR"


async def test_tools_never_raise_on_bad_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="not json")

    monkeypatch.setattr(applications, "TRANSPORT", httpx.MockTransport(handler))

    access = await applications.get_application_access(_EMPLOYEE_ID)
    assert access["applications"] == []
    assert access["error"]["code"] == "BAD_RESPONSE"
