"""Tests for the Access agent's MockWorld tools against the REAL world app.

Every tool is exercised over in-process ASGI against the real ``/world/access``
routes and the canonical seed (E42, GRP-STANDARD granted, GRP-PRIVILEGED).
Assertions cover the happy path, the unknown-employee signal, error envelopes
(domain enforcement + validation), the ``X-Agent-Id`` header, and the
never-raise contract on transport failure.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ..tools import access

_EMPLOYEE_ID = "E42"


class RecordingTransport(httpx.AsyncBaseTransport):
    """ASGI transport that records every request the access tools make."""

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
    """Route the access tools at MockWorld through a recording transport."""
    recorder = RecordingTransport(world_app)
    monkeypatch.setattr(access, "TRANSPORT", recorder)
    return recorder


async def test_get_access_summary(recording_transport: RecordingTransport) -> None:
    result = await access.get_access_summary(_EMPLOYEE_ID)

    assert result["identity"]["username"] == "eva.starter"
    assert result["identity"]["status"] == "created"
    assert [
        (entitlement["group_id"], entitlement["status"])
        for entitlement in result["entitlements"]
    ] == [("GRP-STANDARD", "granted")]
    assert [(group["id"], group["kind"]) for group in result["groups"]] == [
        ("GRP-STANDARD", "baseline")
    ]


async def test_get_access_summary_unknown_employee(
    recording_transport: RecordingTransport,
) -> None:
    """The access domain's not-found signal: null identity, empty lists."""
    result = await access.get_access_summary("E404")

    assert result == {"identity": None, "entitlements": [], "groups": []}


async def test_request_group_access(recording_transport: RecordingTransport) -> None:
    result = await access.request_group_access(
        _EMPLOYEE_ID, "GRP-PRIVILEGED", "onboarding privileged access"
    )

    assert result["requested"] is True
    request = result["request"]
    assert request["id"] == "REQ-1"
    assert request["employee_id"] == _EMPLOYEE_ID
    assert request["group_id"] == "GRP-PRIVILEGED"
    assert request["description"] == "onboarding privileged access"
    assert request["status"] == "requested"


async def test_list_access_requests(recording_transport: RecordingTransport) -> None:
    assert (await access.list_access_requests(_EMPLOYEE_ID))["requests"] == []

    await access.request_group_access(_EMPLOYEE_ID, "GRP-PRIVILEGED")
    result = await access.list_access_requests(_EMPLOYEE_ID)

    assert [request["group_id"] for request in result["requests"]] == ["GRP-PRIVILEGED"]
    assert result["requests"][0]["status"] == "requested"


async def test_sends_x_agent_id_header(recording_transport: RecordingTransport) -> None:
    await access.get_access_summary(_EMPLOYEE_ID)

    assert recording_transport.requests
    for request in recording_transport.requests:
        assert request.headers.get("x-agent-id") == "access-agent"


async def test_tools_only_touch_access_routes(
    recording_transport: RecordingTransport,
) -> None:
    await access.get_access_summary(_EMPLOYEE_ID)
    await access.request_group_access(_EMPLOYEE_ID, "GRP-PRIVILEGED")
    await access.list_access_requests(_EMPLOYEE_ID)

    paths = {request.url.path for request in recording_transport.requests}
    assert all(path.startswith("/world/access/") for path in paths)
    assert not any(path.startswith("/simulation/") for path in paths)


async def test_domain_enforcement_error_envelope(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the access domain grant, the world rejects with FORBIDDEN."""
    monkeypatch.setenv("ALLOWED_DOMAINS", "device-agent:devices")
    monkeypatch.setattr(access, "TRANSPORT", httpx.ASGITransport(app=world_app))

    summary = await access.get_access_summary(_EMPLOYEE_ID)
    assert summary["error"]["code"] == "FORBIDDEN"

    result = await access.request_group_access(_EMPLOYEE_ID, "GRP-PRIVILEGED")
    assert result == {
        "requested": False,
        "code": "FORBIDDEN",
        "description": "Agent 'access-agent' is not permitted in domain 'access'",
    }

    listed = await access.list_access_requests(_EMPLOYEE_ID)
    assert listed["requests"] == []
    assert listed["error"]["code"] == "FORBIDDEN"


async def test_request_validation_error_envelope(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body FastAPI rejects surfaces as the VALIDATION_ERROR envelope."""

    async def raw_post() -> httpx.Response:
        transport = httpx.ASGITransport(app=world_app)
        async with httpx.AsyncClient(
            base_url="http://mockworld",
            headers={"X-Agent-Id": "access-agent"},
            transport=transport,
        ) as client:
            return await client.post(
                f"/world/access/{_EMPLOYEE_ID}/request", json={"description": "no group"}
            )

    response = await raw_post()
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_tools_never_raise_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(access, "TRANSPORT", httpx.MockTransport(handler))

    summary = await access.get_access_summary(_EMPLOYEE_ID)
    assert summary["error"]["code"] == "NETWORK_ERROR"

    result = await access.request_group_access(_EMPLOYEE_ID, "GRP-PRIVILEGED")
    assert result["requested"] is False
    assert result["code"] == "NETWORK_ERROR"

    listed = await access.list_access_requests(_EMPLOYEE_ID)
    assert listed["requests"] == []
    assert listed["error"]["code"] == "NETWORK_ERROR"


async def test_tools_never_raise_on_bad_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="not json")

    monkeypatch.setattr(access, "TRANSPORT", httpx.MockTransport(handler))

    summary = await access.get_access_summary(_EMPLOYEE_ID)
    assert summary["error"]["code"] == "BAD_RESPONSE"

    result: dict[str, Any] = await access.request_group_access(
        _EMPLOYEE_ID, "GRP-PRIVILEGED"
    )
    assert result["requested"] is False
    assert result["code"] == "BAD_RESPONSE"
