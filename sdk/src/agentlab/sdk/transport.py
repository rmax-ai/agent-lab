"""Agent transport boundary (SPEC §14, DEC-12, DEC-16 wire shape).

``AgentTransport`` is the swappable seam between an agent and the rest of the
lab. The hackathon adapter is :class:`AgentLabTransport`, a `websockets` client
to the backend's ``/ws/agents`` hub. A future ``SlackTransport`` presents the
same four methods.

Outbound frames are always the flat JSON envelope::

    {"type": <msg_type>, "agent_id": <agent_id>, "payload": <payload>}

``send`` additionally carries ``channel`` and ``message`` keys (SPEC §13
channels). Everything is snake_case JSON, never nested model-of-model lists.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from agentlab.sdk.protocols import WorkflowRequest, WorkflowStatus

_SEND_TIMEOUT_SECONDS = 10.0

_CHANNEL_MESSAGE = "channel_message"
_WORKFLOW_REQUEST = "workflow_request"
_WORKFLOW_STATUS = "workflow_status"


def _to_ws_uri(base_url: str) -> str:
    """Turn an ``http(s)://`` base URL into a ``ws(s)://`` hub URI."""
    if base_url.startswith(("ws://", "wss://")):
        return base_url
    if base_url.startswith("https://"):
        return f"wss://{base_url[len('https://'):]}"
    if base_url.startswith("http://"):
        return f"ws://{base_url[len('http://'):]}"
    return f"ws://{base_url}"


class AgentTransport(ABC):
    """Abstract transport contract shared by every adapter (SPEC §14)."""

    @abstractmethod
    async def send(self, channel: str, message: str) -> None:
        """Publish a natural-language message to ``channel``."""

    @abstractmethod
    async def subscribe(
        self,
        channel: str,
        callback: Callable[[dict], Awaitable[None]],
    ) -> None:
        """Register ``callback`` for inbound messages on ``channel``."""

    @abstractmethod
    async def delegate(self, request: WorkflowRequest) -> None:
        """Delegate an outcome to a peer agent."""

    @abstractmethod
    async def report_status(self, status: WorkflowStatus) -> None:
        """Report the current state of a workflow to the coordinator."""


class AgentLabTransport(AgentTransport):
    """WebSocket transport to the Agent Lab hub (SPEC §14, DEC-12)."""

    def __init__(self, base_url: str, agent_id: str, token: str | None = None) -> None:
        self._agent_id = agent_id
        self._token = token
        self._uri = _to_ws_uri(base_url)
        self._connection: ClientConnection | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._subscribers: dict[str, list[Callable[[dict], Awaitable[None]]]] = {}

    async def connect(self) -> None:
        """Open the WebSocket and start the receive loop."""
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._connection = await websockets.connect(
            self._uri,
            additional_headers=headers or None,
            open_timeout=_SEND_TIMEOUT_SECONDS,
        )
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def disconnect(self) -> None:
        """Stop the receive loop and close the connection."""
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def __aenter__(self) -> AgentLabTransport:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.disconnect()

    async def _send_frame(self, frame: dict[str, Any]) -> None:
        if self._connection is None:
            raise RuntimeError("Not connected: call connect() before sending.")
        payload = json.dumps(frame)
        await asyncio.wait_for(
            self._connection.send(payload),
            timeout=_SEND_TIMEOUT_SECONDS,
        )

    async def send(self, channel: str, message: str) -> None:
        await self._send_frame(
            {
                "type": _CHANNEL_MESSAGE,
                "agent_id": self._agent_id,
                "channel": channel,
                "message": message,
            }
        )

    async def subscribe(
        self,
        channel: str,
        callback: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._subscribers.setdefault(channel, []).append(callback)

    async def delegate(self, request: WorkflowRequest) -> None:
        await self._send_frame(
            {
                "type": _WORKFLOW_REQUEST,
                "agent_id": self._agent_id,
                "payload": request.model_dump(mode="json"),
            }
        )

    async def report_status(self, status: WorkflowStatus) -> None:
        await self._send_frame(
            {
                "type": _WORKFLOW_STATUS,
                "agent_id": self._agent_id,
                "payload": status.model_dump(mode="json"),
            }
        )

    async def _recv_loop(self) -> None:
        assert self._connection is not None
        async for raw in self._connection:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict) or frame.get("type") != _CHANNEL_MESSAGE:
                continue
            channel = frame.get("channel")
            for callback in list(self._subscribers.get(channel, [])):
                await callback(frame)
