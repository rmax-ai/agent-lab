"""Scripted WebSocket server tests for AgentLabTransport (SPEC §14 wire shape)."""

from __future__ import annotations

import asyncio
import json

import websockets
from websockets.asyncio.server import ServerConnection

from agentlab.sdk.protocols import WorkflowRequest
from agentlab.sdk.transport import AgentLabTransport


async def test_transport_send_subscribe_delegate() -> None:
    server_received: list[dict] = []
    server_got_workflow = asyncio.Event()

    async def handler(ws: ServerConnection) -> None:
        async for raw in ws:
            frame = json.loads(raw)
            server_received.append(frame)
            if frame.get("type") == "channel_message":
                await ws.send(
                    json.dumps(
                        {
                            "type": "channel_message",
                            "channel": "devices",
                            "message": "hello-device",
                            "agent_id": "onboarding-agent",
                        }
                    )
                )
            if frame.get("type") == "workflow_request":
                server_got_workflow.set()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    sockets = list(server.sockets)
    port = sockets[0].getsockname()[1]

    inbound: list[dict] = []
    inbound_done = asyncio.Event()

    async def on_message(frame: dict) -> None:
        inbound.append(frame)
        inbound_done.set()

    transport = AgentLabTransport(f"ws://127.0.0.1:{port}", agent_id="device-agent")
    await transport.connect()
    await transport.subscribe("devices", on_message)

    await transport.send("devices", "hello")
    await asyncio.wait_for(inbound_done.wait(), timeout=2.0)

    await transport.delegate(
        WorkflowRequest(
            workflow_id="WF-D-42",
            case_id="ONB-42",
            goal="employee_device_ready",
            employee_id="E42",
            context={"start_date": "2026-09-07"},
        )
    )
    await asyncio.wait_for(server_got_workflow.wait(), timeout=2.0)

    await transport.disconnect()
    server.close()
    await server.wait_closed()

    send_frame = next(
        frame
        for frame in server_received
        if frame.get("type") == "channel_message" and frame.get("message") == "hello"
    )
    assert send_frame["agent_id"] == "device-agent"
    assert send_frame["channel"] == "devices"
    assert send_frame["message"] == "hello"

    workflow_frame = next(
        frame for frame in server_received if frame.get("type") == "workflow_request"
    )
    assert workflow_frame["agent_id"] == "device-agent"
    assert workflow_frame["payload"]["workflow_id"] == "WF-D-42"
    assert workflow_frame["payload"]["goal"] == "employee_device_ready"

    assert inbound
    assert inbound[0]["type"] == "channel_message"
    assert inbound[0]["channel"] == "devices"
    assert inbound[0]["message"] == "hello-device"
