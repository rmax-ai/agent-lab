"""Hub reconnect + stale-subscriber tests (SPEC §13/§14).

Covers the hub side of reconnection: subscriptions are set-based (a repeat
subscribe of the same socket is idempotent), a reconnected agent receives each
frame exactly once, and a subscriber whose socket dies mid-subscription is
dropped from the fanout sets on the next failed send instead of lingering.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import websockets

from agentlab.backend import db
from agentlab.backend.app import create_app
from agentlab.backend.hub import ChannelHub

ONBOARDING = "onboarding-agent"
DEVICE = "device-agent"


class _FakeSocket:
    """Minimal stand-in for a hub-side WebSocket."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, frame: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("socket gone")
        self.sent.append(frame)


async def test_subscribe_is_idempotent_per_socket() -> None:
    hub = ChannelHub()
    socket = _FakeSocket()

    hub._subscribe(socket, DEVICE)  # type: ignore[arg-type]
    hub._subscribe(socket, DEVICE)  # reconnect-style repeat: still one entry
    await hub.broadcast("#devices", {"type": "channel_message", "message": "hi"})

    assert [f["message"] for f in socket.sent] == ["hi"]
    assert len(hub._subscribers["#devices"]) == 1


async def test_failed_send_drops_dead_subscriber() -> None:
    hub = ChannelHub()
    dead = _FakeSocket(fail=True)
    alive = _FakeSocket()
    hub._subscribe(dead, DEVICE)  # type: ignore[arg-type]
    hub._subscribe(alive, "access-agent")  # type: ignore[arg-type]

    await hub.broadcast("#devices", {"type": "channel_message", "message": "hi"})

    assert [f["message"] for f in alive.sent] == ["hi"]
    for subscribers in hub._subscribers.values():
        assert dead not in subscribers
    # The healthy subscriber is untouched.
    assert any(alive in subscribers for subscribers in hub._subscribers.values())


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _connect_agent(ws_url: str, agent_id: str) -> websockets.ClientConnection:
    ws = await websockets.connect(ws_url)
    await ws.send(json.dumps({"type": "hello", "agent_id": agent_id}))
    welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert welcome["type"] == "welcome"
    return ws


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


async def test_reconnected_agent_receives_each_frame_once(live_server: str) -> None:
    ws_url = live_server.replace("http://", "ws://") + "/ws/agents"
    coordinator = await _connect_agent(ws_url, ONBOARDING)
    first = await _connect_agent(ws_url, DEVICE)
    try:
        # Drop the device socket; the coordinator observes the offline
        # broadcast, which guarantees the hub finished unsubscribing it.
        await first.close()
        offline = json.loads(await asyncio.wait_for(coordinator.recv(), timeout=5))
        assert offline["type"] == "agent_status"
        assert offline["agent_id"] == DEVICE
        assert offline["status"] == "offline"

        second = await _connect_agent(ws_url, DEVICE)
        try:
            await coordinator.send(
                json.dumps(
                    {
                        "type": "channel_message",
                        "agent_id": ONBOARDING,
                        "channel": "#devices",
                        "message": "after reconnect",
                    }
                )
            )
            received = json.loads(await asyncio.wait_for(second.recv(), timeout=5))
            assert received["type"] == "channel_message"
            assert received["message"] == "after reconnect"

            # No duplicate or stale frame follows on the reconnected socket.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(second.recv(), timeout=0.5)

            # The registry holds exactly one entry for the agent, online.
            async with httpx.AsyncClient(base_url=live_server) as client:
                agents = (await client.get("/agents")).json()["agents"]
                device_entries = [a for a in agents if a["agent_id"] == DEVICE]
                assert len(device_entries) == 1
                assert device_entries[0]["status"] == "online"
        finally:
            await second.close()
    finally:
        await coordinator.close()
