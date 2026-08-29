"""WebSocket hub and agent registry (SPEC §13/§14/§26, PATTERNS §3).

``ChannelHub`` is the Agent Router. It registers agents on a ``hello`` frame,
fans natural-language messages out to channel subscribers, routes structured
``workflow_request``/``workflow_status`` frames, and keeps the UI informed over
the ``#onboarding`` channel. Registry state is in-process only; channel messages
and workflow-status events are persisted through the backend's ``db`` sessions.

The hub is used from a single asyncio loop (FastAPI's), so the plain-dict
registry needs no locking.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import WebSocket
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect

from agentlab.backend import db
from agentlab.backend.channels import CHANNELS, PRIVATE_PREFIX
from agentlab.backend.events import emit_event
from agentlab.backend.models import ChannelMessageRow, WorkflowRun, utcnow

_HELLO_TIMEOUT_SECONDS = 10.0
_HEARTBEAT_INTERVAL_SECONDS = 30.0

_HELLO = "hello"
_WELCOME = "welcome"
_CHANNEL_MESSAGE = "channel_message"
_WORKFLOW_REQUEST = "workflow_request"
_WORKFLOW_STATUS = "workflow_status"
_EVENT = "event"
_AGENT_STATUS = "agent_status"
_PING = "ping"

_STATUS_ONLINE = "online"
_STATUS_OFFLINE = "offline"

_ONBOARDING_CHANNEL = "#onboarding"
_WORKFLOW_STATUS_TYPE = "WORKFLOW_STATUS"


class ChannelHub:
    """In-memory agent registry plus WebSocket channel fanout (SPEC §13)."""

    def __init__(self) -> None:
        self.registry: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, set[WebSocket]] = {}

    # -- registry ------------------------------------------------------------

    def register_agent(
        self,
        agent_id: str,
        tools: int | None = None,
        knowledge_docs: int | None = None,
    ) -> dict[str, Any]:
        """Register (or re-register) an agent and mark it ``online``."""
        entry = self.registry.setdefault(agent_id, {})
        entry.update(
            {
                "status": _STATUS_ONLINE,
                "connected_at": utcnow(),
                "tools": int(tools or 0),
                "knowledge_docs": int(knowledge_docs or 0),
            }
        )
        return self._serialize(agent_id, entry)

    def unregister_agent(self, agent_id: str) -> None:
        """Mark an agent ``offline`` without removing its registry row."""
        entry = self.registry.get(agent_id)
        if entry is not None:
            entry["status"] = _STATUS_OFFLINE

    def list_agents(self) -> list[dict[str, Any]]:
        """Return all known agents, online and offline."""
        return [
            self._serialize(agent_id, entry)
            for agent_id, entry in self.registry.items()
        ]

    # -- channel membership ---------------------------------------------------

    def _subscribe(self, websocket: WebSocket, agent_id: str) -> None:
        """Join a connection to every public channel plus its private channel."""
        for channel in (*CHANNELS, f"{PRIVATE_PREFIX}{agent_id}"):
            self._subscribers.setdefault(channel, set()).add(websocket)

    def _unsubscribe(self, websocket: WebSocket) -> None:
        """Drop a connection from every channel it subscribed to."""
        for subscribers in self._subscribers.values():
            subscribers.discard(websocket)

    # -- fanout ---------------------------------------------------------------

    async def broadcast(self, channel: str, frame: dict[str, Any]) -> None:
        """Deliver ``frame`` to every connection subscribed to ``channel``."""
        for websocket in list(self._subscribers.get(channel, ())):
            await self._safe_send(websocket, frame)

    async def send_to_agent(self, agent_id: str, frame: dict[str, Any]) -> None:
        """Deliver ``frame`` to every live connection owned by ``agent_id``."""
        await self.broadcast(f"{PRIVATE_PREFIX}{agent_id}", frame)

    async def _safe_send(self, websocket: WebSocket, frame: dict[str, Any]) -> None:
        """Best-effort send; a dead subscriber is skipped, not fatal."""
        with contextlib.suppress(Exception):
            await websocket.send_json(frame)

    # -- persistence ----------------------------------------------------------

    def _persist_message(self, channel: str, agent_id: str, message: str) -> None:
        with db.session_scope() as session:
            session.add(
                ChannelMessageRow(channel=channel, agent_id=agent_id, message=message)
            )
            session.commit()

    def _emit_workflow_status(self, agent_id: str, payload: dict[str, Any]) -> None:
        workflow_id = payload.get("workflow_id")
        with db.session_scope() as session:
            case_id = self._case_id_for(session, workflow_id)
            emit_event(
                session,
                case_id,
                workflow_id if workflow_id is None else str(workflow_id),
                agent_id,
                _WORKFLOW_STATUS_TYPE,
                payload,
            )
            session.commit()

    @staticmethod
    def _case_id_for(session: Session, workflow_id: Any) -> str:
        if workflow_id is not None:
            run = session.get(WorkflowRun, str(workflow_id))
            if run is not None:
                return run.case_id
        return ""

    # -- connection lifecycle --------------------------------------------------

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Run one agent connection: ``hello`` → ``welcome`` → dispatch loop."""
        await websocket.accept()
        agent_id: str | None = None
        heartbeat: asyncio.Task[None] | None = None
        try:
            try:
                frame = await asyncio.wait_for(
                    websocket.receive_json(), timeout=_HELLO_TIMEOUT_SECONDS
                )
            except TimeoutError:
                return
            if not isinstance(frame, dict) or frame.get("type") != _HELLO:
                return
            hello_agent_id = frame.get("agent_id")
            if not isinstance(hello_agent_id, str) or not hello_agent_id.strip():
                return
            agent_id = hello_agent_id.strip()
            self.register_agent(
                agent_id, frame.get("tools"), frame.get("knowledge_docs")
            )
            self._subscribe(websocket, agent_id)
            await websocket.send_json(self._welcome(agent_id))
            heartbeat = asyncio.create_task(self._heartbeat(websocket))

            while True:
                raw = await websocket.receive_json()
                if isinstance(raw, dict):
                    await self._dispatch(agent_id, websocket, raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
            self._unsubscribe(websocket)
            if agent_id is not None:
                self.unregister_agent(agent_id)
                await self.broadcast(
                    _ONBOARDING_CHANNEL,
                    {
                        "type": _AGENT_STATUS,
                        "agent_id": agent_id,
                        "status": _STATUS_OFFLINE,
                    },
                )

    async def _heartbeat(self, websocket: WebSocket) -> None:
        """Ping the client periodically; exit when the connection is gone."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            try:
                await websocket.send_json({"type": _PING})
            except Exception:
                return

    def _welcome(self, agent_id: str) -> dict[str, Any]:
        return {
            "type": _WELCOME,
            "agent_id": agent_id,
            "channels": [*CHANNELS, f"{PRIVATE_PREFIX}{agent_id}"],
            "online_agents": self._online_agent_ids(),
        }

    def _online_agent_ids(self) -> list[str]:
        return [
            agent_id
            for agent_id, entry in self.registry.items()
            if entry["status"] == _STATUS_ONLINE
        ]

    async def _dispatch(
        self,
        agent_id: str,
        sender: WebSocket,
        frame: dict[str, Any],
    ) -> None:
        frame_type = frame.get("type")
        if frame_type == _CHANNEL_MESSAGE:
            channel = frame.get("channel")
            message = frame.get("message")
            if isinstance(channel, str) and isinstance(message, str):
                self._persist_message(channel, agent_id, message)
                recipients = self._subscribers.get(channel, set()) - {sender}
                outbound = {
                    "type": _CHANNEL_MESSAGE,
                    "agent_id": agent_id,
                    "channel": channel,
                    "message": message,
                }
                for websocket in list(recipients):
                    await self._safe_send(websocket, outbound)
        elif frame_type == _WORKFLOW_REQUEST:
            payload = frame.get("payload")
            if isinstance(payload, dict) and payload.get("target_agent_id"):
                await self.send_to_agent(str(payload["target_agent_id"]), frame)
            else:
                await self.broadcast(_ONBOARDING_CHANNEL, frame)
        elif frame_type == _WORKFLOW_STATUS:
            payload = frame.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            await self.broadcast(_ONBOARDING_CHANNEL, frame)
            self._emit_workflow_status(agent_id, payload)
        elif frame_type == _EVENT:
            await self.broadcast(
                _ONBOARDING_CHANNEL,
                {"type": _EVENT, "payload": frame.get("payload")},
            )

    @staticmethod
    def _serialize(agent_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        connected_at = entry["connected_at"]
        return {
            "agent_id": agent_id,
            "status": entry["status"],
            "connected_at": connected_at.isoformat() if connected_at is not None else None,
            "tools": entry.get("tools", 0),
            "knowledge_docs": entry.get("knowledge_docs", 0),
        }
