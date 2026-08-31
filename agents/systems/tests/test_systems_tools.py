"""Tests for the Systems agent's tools against the REAL world + backend apps.

Every tool is exercised over in-process ASGI: reads against the real
``/world/systems`` and shared ``/world/employees`` routes with the canonical
seed (E42, SYS-EMAIL/SYS-VPN/SYS-HR, NO SystemAccount rows — every account
starts ``missing``), and ``provision_account`` against the real backend
``/tasks`` flow (the systems world surface is read-only). Assertions cover
the happy path, the unknown-employee signal, error envelopes (domain
enforcement + validation), the ``X-Agent-Id`` header, and the never-raise
contract on transport failure.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from agentlab.world import db as world_db
from agentlab.world.models import SystemAccount

from ..tools import systems

_EMPLOYEE_ID = "E42"


class RecordingTransport(httpx.AsyncBaseTransport):
    """ASGI transport that records every request the systems tools make."""

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
    """Route the systems tools at MockWorld through a recording transport."""
    recorder = RecordingTransport(world_app)
    monkeypatch.setattr(systems, "TRANSPORT", recorder)
    return recorder


def _add_account(account_id: str, system_id: str, status: str) -> None:
    """World-operator row setup: the agent can never create these itself."""
    with world_db.session_scope() as session:
        session.add(
            SystemAccount(
                id=account_id,
                employee_id=_EMPLOYEE_ID,
                system_id=system_id,
                status=status,
            )
        )
        session.commit()


async def test_get_required_systems(recording_transport: RecordingTransport) -> None:
    result = await systems.get_required_systems(_EMPLOYEE_ID)

    assert result["employee_id"] == _EMPLOYEE_ID
    assert result["role"] == "Software Engineer"
    assert result["manager_id"] == "M1"
    assert result["required_systems"] == ["SYS-EMAIL", "SYS-VPN"]
    assert result["hr_required"] is False


async def test_get_required_systems_unknown_employee(
    recording_transport: RecordingTransport,
) -> None:
    """An employee unknown to the world surfaces the NOT_FOUND envelope."""
    result = await systems.get_required_systems("E404")

    assert result["error"]["code"] == "NOT_FOUND"


async def test_get_account_status_seed(recording_transport: RecordingTransport) -> None:
    """No SystemAccount rows are seeded: every account starts missing."""
    result = await systems.get_account_status(_EMPLOYEE_ID)

    assert result["employee_id"] == _EMPLOYEE_ID
    assert [
        (row["system_id"], row["account_status"]) for row in result["accounts"]
    ] == [
        ("SYS-EMAIL", "missing"),
        ("SYS-HR", "missing"),
        ("SYS-VPN", "missing"),
    ]


async def test_get_account_status_with_rows(
    recording_transport: RecordingTransport,
) -> None:
    _add_account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active")
    _add_account("SYSACC-E42-VPN", "SYS-VPN", "pending")

    result = await systems.get_account_status(_EMPLOYEE_ID)

    assert {row["system_id"]: row["account_status"] for row in result["accounts"]} == {
        "SYS-EMAIL": "active",
        "SYS-HR": "missing",
        "SYS-VPN": "pending",
    }


async def test_provision_account_opens_real_it_task(
    recording_transport: RecordingTransport,
    systems_backend_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """Provisioning is a REAL backend HumanTask (an IT ticket), not a world call."""
    del systems_backend_transport  # monkeypatched; used implicitly by the tool
    result = await systems.provision_account(
        _EMPLOYEE_ID, "SYS-VPN", case_id="ONB-E42-TOOLS", workflow_id="WF-TOOLS-1"
    )

    assert result["task_opened"] is True
    task = result["task"]
    assert task["human_task_id"] == "HT-ONB-E42-TOOLS-SYS-VPN"
    assert task["case_id"] == "ONB-E42-TOOLS"
    assert task["workflow_id"] == "WF-TOOLS-1"
    assert task["requested_by"] == "systems-agent"
    assert task["requested_from"] == "it-support"
    assert task["type"] == "MANUAL_ACTION"
    assert task["status"] == "open"
    assert task["context"]["system_id"] == "SYS-VPN"

    # The task row is really persisted in the backend task store.
    fetched = await backend_client.get(f"/tasks/{task['human_task_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["case_id"] == "ONB-E42-TOOLS"

    # The read-only systems surface was never touched (and no world call at
    # all happens for provisioning — the tool only talks to the backend).
    assert recording_transport.requests == []


async def test_verify_account_all_active(recording_transport: RecordingTransport) -> None:
    _add_account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active")
    _add_account("SYSACC-E42-VPN", "SYS-VPN", "active")

    result = await systems.verify_account(_EMPLOYEE_ID)

    assert result["all_required_verified"] is True
    assert result["verified"] == {"SYS-EMAIL": True, "SYS-HR": False, "SYS-VPN": True}
    assert result["missing_required"] == []
    assert result["pending_required"] == []
    assert result["policy_violations"] == []
    assert result["accounts"]["SYS-HR"] == "missing"  # correct for a non-manager


async def test_verify_account_missing_row_is_never_verified(
    recording_transport: RecordingTransport,
) -> None:
    _add_account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active")

    result = await systems.verify_account(_EMPLOYEE_ID)

    assert result["all_required_verified"] is False
    assert result["verified"]["SYS-VPN"] is False  # absent row: never verified
    assert result["missing_required"] == ["SYS-VPN"]


async def test_verify_account_pending_is_never_verified(
    recording_transport: RecordingTransport,
) -> None:
    _add_account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active")
    _add_account("SYSACC-E42-VPN", "SYS-VPN", "pending")

    result = await systems.verify_account(_EMPLOYEE_ID)

    assert result["all_required_verified"] is False
    assert result["verified"]["SYS-VPN"] is False  # pending: never verified
    assert result["pending_required"] == ["SYS-VPN"]


async def test_verify_account_flags_hr_account_for_non_manager(
    recording_transport: RecordingTransport,
) -> None:
    """An HR account for a non-manager is a violation, never a verification."""
    _add_account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active")
    _add_account("SYSACC-E42-VPN", "SYS-VPN", "active")
    _add_account("SYSACC-E42-HR", "SYS-HR", "active")

    result = await systems.verify_account(_EMPLOYEE_ID)

    assert result["policy_violations"] == ["hr_account_for_non_manager"]
    assert result["verified"]["SYS-HR"] is False


async def test_sends_x_agent_id_header(
    recording_transport: RecordingTransport,
    systems_backend_transport: httpx.ASGITransport,
) -> None:
    del systems_backend_transport  # monkeypatched; used implicitly by the tool
    await systems.get_required_systems(_EMPLOYEE_ID)
    await systems.get_account_status(_EMPLOYEE_ID)
    await systems.provision_account(_EMPLOYEE_ID, "SYS-VPN", case_id="ONB-E42-HDR")

    assert recording_transport.requests
    for request in recording_transport.requests:
        assert request.headers.get("x-agent-id") == "systems-agent"


async def test_tools_only_touch_systems_and_shared_routes(
    recording_transport: RecordingTransport,
) -> None:
    await systems.get_required_systems(_EMPLOYEE_ID)
    await systems.get_account_status(_EMPLOYEE_ID)
    await systems.verify_account(_EMPLOYEE_ID)

    paths = {request.url.path for request in recording_transport.requests}
    assert all(
        path.startswith("/world/systems/") or path.startswith("/world/employees/")
        for path in paths
    )
    assert not any(path.startswith("/simulation/") for path in paths)
    # The systems surface is read-only: the tools never POST to the world.
    assert all(request.method == "GET" for request in recording_transport.requests)


async def test_domain_enforcement_error_envelope(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the systems domain grant, the world rejects with FORBIDDEN."""
    monkeypatch.setenv("ALLOWED_DOMAINS", "access-agent:access")
    monkeypatch.setattr(systems, "TRANSPORT", httpx.ASGITransport(app=world_app))

    status = await systems.get_account_status(_EMPLOYEE_ID)
    assert status["accounts"] == []
    assert status["error"] == {
        "code": "FORBIDDEN",
        "description": "Agent 'systems-agent' is not permitted in domain 'systems'",
    }

    # The shared employees route requires a REGISTERED agent (403 as well).
    required = await systems.get_required_systems(_EMPLOYEE_ID)
    assert required["error"]["code"] == "FORBIDDEN"


async def test_tools_never_raise_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(systems, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(systems, "BACKEND_TRANSPORT", httpx.MockTransport(handler))

    required = await systems.get_required_systems(_EMPLOYEE_ID)
    assert required["error"]["code"] == "NETWORK_ERROR"

    status = await systems.get_account_status(_EMPLOYEE_ID)
    assert status["accounts"] == []
    assert status["error"]["code"] == "NETWORK_ERROR"

    result = await systems.provision_account(_EMPLOYEE_ID, "SYS-VPN")
    assert result["task_opened"] is False
    assert result["code"] == "NETWORK_ERROR"

    verified = await systems.verify_account(_EMPLOYEE_ID)
    assert verified["all_required_verified"] is False
    assert verified["error"]["code"] == "NETWORK_ERROR"


async def test_tools_never_raise_on_bad_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="not json")

    monkeypatch.setattr(systems, "TRANSPORT", httpx.MockTransport(handler))

    status = await systems.get_account_status(_EMPLOYEE_ID)
    assert status["accounts"] == []
    assert status["error"]["code"] == "BAD_RESPONSE"
