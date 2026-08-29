"""WebSocket hub + channel service tests (SPEC §13/§14/§26).

These run a real uvicorn server in a thread and drive it with a ``websockets``
client for the hub plus ``httpx`` for the REST surface (agent registry and
channel history). This exercises the full ASGI WebSocket path, not a simulated
one.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets

from agentlab.backend import db
from agentlab.backend.app import create_app

ONBOARDING = "onboarding-agent"
DEVICE = "device-agent"
ACCESS = "access-agent"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _ws_url(base_url: str) -> str:
    return base_url.replace("http://", "ws://") + "/ws/agents"


async def _connect_agent(
    ws_url: str,
    agent_id: str,
    tools: int = 0,
    knowledge_docs: int = 0,
) -> tuple[websockets.ClientConnection, dict]:
    """Connect a websocket, send its ``hello`` frame, and return it plus welcome."""
    ws = await websockets.connect(ws_url)
    await ws.send(
        json.dumps(
            {
                "type": "hello",
                "agent_id": agent_id,
                "tools": tools,
                "knowledge_docs": knowledge_docs,
            }
        )
    )
    welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert welcome["type"] == "welcome"
    return ws, welcome


@pytest.fixture
async def live_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[str]:
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "hub.db"))
    monkeypatch.delenv("ALLOW_ANY_RESOLVER", raising=False)
    db.reset_engine()

    app = create_app()
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn server failed to start"

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


async def test_hello_registers_agent(live_server: str) -> None:
    ws = await websockets.connect(_ws_url(live_server))
    try:
        await ws.send(json.dumps({"type": "hello", "agent_id": DEVICE, "tools": 3}))
        welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert welcome["type"] == "welcome"
        assert welcome["agent_id"] == DEVICE
        assert "#devices" in welcome["channels"]
        assert f"agent:{DEVICE}" in welcome["channels"]
        assert DEVICE in welcome["online_agents"]

        async with httpx.AsyncClient(base_url=live_server) as client:
            resp = await client.get("/agents")
            assert resp.status_code == 200
            agents = resp.json()["agents"]
            assert any(
                a["agent_id"] == DEVICE and a["status"] == "online" for a in agents
            )
            assert agents[0]["tools"] == 3
    finally:
        await ws.close()


async def test_channel_message_fanout_between_clients(live_server: str) -> None:
    ws_url = _ws_url(live_server)
    coordinator, _ = await _connect_agent(ws_url, ONBOARDING)
    device, _ = await _connect_agent(ws_url, DEVICE)
    try:
        await coordinator.send(
            json.dumps(
                {
                    "type": "channel_message",
                    "agent_id": ONBOARDING,
                    "channel": "#devices",
                    "message": "hello device",
                }
            )
        )
        received = json.loads(await asyncio.wait_for(device.recv(), timeout=5))
        assert received["type"] == "channel_message"
        assert received["channel"] == "#devices"
        assert received["agent_id"] == ONBOARDING
        assert received["message"] == "hello device"
    finally:
        await coordinator.close()
        await device.close()


async def test_message_persists_to_history(live_server: str) -> None:
    ws_url = _ws_url(live_server)
    device, _ = await _connect_agent(ws_url, DEVICE)
    await device.send(
        json.dumps(
            {
                "type": "channel_message",
                "agent_id": DEVICE,
                "channel": "#devices",
                "message": "persisted note",
            }
        )
    )
    await device.close()

    async with httpx.AsyncClient(base_url=live_server) as client:
        resp = await client.get("/channels/%23devices/messages")
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        assert [m["message"] for m in messages] == ["persisted note"]
        assert messages[0]["agent_id"] == DEVICE
        assert messages[0]["channel"] == "#devices"


async def test_agent_status_offline_on_disconnect(live_server: str) -> None:
    ws_url = _ws_url(live_server)
    coordinator, _ = await _connect_agent(ws_url, ONBOARDING)
    device, _ = await _connect_agent(ws_url, DEVICE)
    try:
        await device.close()

        offline = json.loads(await asyncio.wait_for(coordinator.recv(), timeout=5))
        assert offline["type"] == "agent_status"
        assert offline["agent_id"] == DEVICE
        assert offline["status"] == "offline"
    finally:
        await coordinator.close()


async def test_private_channel_delivers_only_to_owner(live_server: str) -> None:
    ws_url = _ws_url(live_server)
    device, _ = await _connect_agent(ws_url, DEVICE)
    access, _ = await _connect_agent(ws_url, ACCESS)
    try:
        await device.send(
            json.dumps(
                {
                    "type": "channel_message",
                    "agent_id": DEVICE,
                    "channel": f"agent:{ACCESS}",
                    "message": "direct to access",
                }
            )
        )

        received = json.loads(await asyncio.wait_for(access.recv(), timeout=5))
        assert received["channel"] == f"agent:{ACCESS}"
        assert received["message"] == "direct to access"
        assert received["agent_id"] == DEVICE

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(device.recv(), timeout=0.5)
    finally:
        await device.close()
        await access.close()


async def test_workflow_request_routed_to_target(live_server: str) -> None:
    ws_url = _ws_url(live_server)
    coordinator, _ = await _connect_agent(ws_url, ONBOARDING)
    device, _ = await _connect_agent(ws_url, DEVICE)
    try:
        request = {
            "type": "workflow_request",
            "agent_id": ONBOARDING,
            "payload": {
                "workflow_id": "WF-D-42",
                "case_id": "ONB-42",
                "goal": "employee_device_ready",
                "employee_id": "E42",
                "context": {},
                "target_agent_id": DEVICE,
            },
        }
        await coordinator.send(json.dumps(request))

        routed = json.loads(await asyncio.wait_for(device.recv(), timeout=5))
        assert routed["type"] == "workflow_request"
        assert routed["payload"]["workflow_id"] == "WF-D-42"
        assert routed["payload"]["target_agent_id"] == DEVICE

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(coordinator.recv(), timeout=0.5)
    finally:
        await coordinator.close()
        await device.close()
