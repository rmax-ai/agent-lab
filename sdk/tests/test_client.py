"""AgentLabClient.emit_event wire tests (SPEC §23, issue #27).

The SDK never imports the backend, so these run against an httpx.MockTransport
that records the request and answers with the route's 201 envelope.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from agentlab.sdk.client import AgentLabClient, AgentLabError
from agentlab.sdk.protocols import Event


def _event() -> Event:
    return Event(
        ts=datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC),
        case_id="ONB-42",
        workflow_id="WF-D-42",
        actor="device-agent",
        type="TOOL_CALL",
        payload={"tool": "reserve_device"},
    )


def _mock_client(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    agent_id: str | None = "device-agent",
    recorded: list[httpx.Request] | None = None,
) -> AgentLabClient:
    def _handler(request: httpx.Request) -> httpx.Response:
        if recorded is not None:
            recorded.append(request)
        if handler is not None:
            return handler(request)
        return httpx.Response(201, json={"ok": True})

    client = AgentLabClient("http://test", agent_id=agent_id)
    # Swap in the mock transport: the client builds its own AsyncClient, and
    # the SDK test boundary keeps the backend out of scope. The constructor's
    # headers (including X-Agent-Id) carry over so the wire shape is tested.
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        base_url="http://test",
        headers=client._client.headers,
    )
    return client


async def test_emit_event_posts_event_shape_with_agent_header() -> None:
    recorded: list[httpx.Request] = []
    async with _mock_client(recorded=recorded) as client:
        result = await client.emit_event(_event())

    assert result == {"ok": True}
    assert len(recorded) == 1
    request = recorded[0]
    assert request.method == "POST"
    assert request.url.path == "/events"
    assert request.headers["X-Agent-Id"] == "device-agent"

    body = json.loads(request.content)
    # The canonical Event wire shape; the server overrides actor and ts.
    assert body["case_id"] == "ONB-42"
    assert body["workflow_id"] == "WF-D-42"
    assert body["actor"] == "device-agent"
    assert body["type"] == "TOOL_CALL"
    assert body["payload"] == {"tool": "reserve_device"}
    assert body["ts"] == "2026-08-30T12:00:00+00:00"


async def test_emit_event_without_agent_id_sends_no_header() -> None:
    recorded: list[httpx.Request] = []
    async with _mock_client(recorded=recorded, agent_id=None) as client:
        await client.emit_event(_event())

    assert "X-Agent-Id" not in recorded[0].headers


async def test_emit_event_surfaces_error_envelope() -> None:
    def not_found(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "NOT_FOUND", "description": "Case 'ONB-42' not found"}},
        )

    async with _mock_client(not_found) as client:
        with pytest.raises(AgentLabError) as exc_info:
            await client.emit_event(_event())

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.message.lower()
