"""Reconnect + failure-mode tests for AgentLabTransport (SPEC §14).

Pins three behaviours: send() on a dead or never-opened socket fails fast with
the typed TransportError (never hangs, never leaks a half-open connection);
connect() after an unexpected drop is clean (the stale receive loop and socket
are torn down, so inbound frames are never double-delivered); and local channel
subscriptions survive a reconnect.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import websockets
from websockets.asyncio.server import ServerConnection

from agentlab.sdk.transport import AgentLabTransport, TransportError

_PROBE_BUDGET = 400  # 400 * 0.005s = 2s worst-case wait for drop detection


async def _serve(
    handler: Callable[[ServerConnection], Awaitable[None]],
) -> tuple[Any, int]:
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = list(server.sockets)[0].getsockname()[1]
    return server, port


async def test_send_before_connect_raises_typed_error() -> None:
    transport = AgentLabTransport("ws://127.0.0.1:1", agent_id="device-agent")
    with pytest.raises(TransportError, match="Not connected"):
        await transport.send("#devices", "never sent")
    # TransportError stays a RuntimeError for backward compatibility.
    assert isinstance(TransportError("x"), RuntimeError)


async def test_send_on_dropped_socket_raises_typed_error() -> None:
    server_connections: list[ServerConnection] = []

    async def handler(ws: ServerConnection) -> None:
        server_connections.append(ws)
        async for _ in ws:
            pass

    server, port = await _serve(handler)
    transport = AgentLabTransport(f"ws://127.0.0.1:{port}", agent_id="device-agent")
    await transport.connect()
    await transport.send("#devices", "before drop")

    # The server side of the socket dies without a client-initiated close.
    await server_connections[0].close()

    # send() must surface a typed error (not hang, not raise a raw library
    # exception) once the drop is noticed.
    for _ in range(_PROBE_BUDGET):
        try:
            await transport.send("#devices", "probe")
        except TransportError:
            break
        await asyncio.sleep(0.005)
    else:
        raise AssertionError("send on a dropped socket never raised TransportError")

    await transport.disconnect()
    server.close()
    await server.wait_closed()


async def test_reconnect_after_drop_is_clean() -> None:
    server_received: list[dict] = []
    connections_opened = 0

    async def handler(ws: ServerConnection) -> None:
        nonlocal connections_opened
        connections_opened += 1
        async for raw in ws:
            frame = json.loads(raw)
            server_received.append(frame)
            # Echo each channel message back on the same socket.
            await ws.send(
                json.dumps(
                    {
                        "type": "channel_message",
                        "channel": frame["channel"],
                        "message": f"echo:{frame['message']}",
                        "agent_id": "server",
                    }
                )
            )

    server, port = await _serve(handler)

    echoes: list[str] = []

    async def on_message(frame: dict) -> None:
        echoes.append(frame["message"])

    transport = AgentLabTransport(f"ws://127.0.0.1:{port}", agent_id="device-agent")
    await transport.subscribe("#devices", on_message)

    await transport.connect()
    await transport.send("#devices", "first")
    for _ in range(_PROBE_BUDGET):
        if echoes:
            break
        await asyncio.sleep(0.005)
    assert echoes == ["echo:first"]

    # Force-close the current socket, then reconnect WITHOUT an explicit
    # disconnect: connect() must tear the stale state down itself.
    assert transport._connection is not None
    await transport._connection.close()  # simulate an unexpected drop
    for _ in range(_PROBE_BUDGET):
        if transport._connection is None:
            break
        await asyncio.sleep(0.005)

    await transport.connect()

    # The subscription registered before the drop still applies, and the
    # message is delivered exactly once (no leaked second receive loop).
    await transport.send("#devices", "second")
    for _ in range(_PROBE_BUDGET):
        if len(echoes) >= 2:
            break
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)  # window for any duplicate delivery to show up

    assert echoes == ["echo:first", "echo:second"]
    assert connections_opened == 2
    sent_messages = [
        frame["message"] for frame in server_received if frame["type"] == "channel_message"
    ]
    assert sent_messages == ["first", "second"]

    await transport.disconnect()
    server.close()
    await server.wait_closed()


async def test_disconnect_is_idempotent() -> None:
    async def handler(ws: ServerConnection) -> None:
        async for _ in ws:
            pass

    server, port = await _serve(handler)
    transport = AgentLabTransport(f"ws://127.0.0.1:{port}", agent_id="device-agent")

    await transport.disconnect()  # never connected: a no-op
    await transport.connect()
    await transport.disconnect()
    await transport.disconnect()  # already disconnected: still a no-op

    server.close()
    await server.wait_closed()
