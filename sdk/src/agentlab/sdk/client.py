"""REST client for the Agent Lab backend (SPEC §13 channels, §15 tasks, §23 events).

:class:`AgentLabClient` is a thin, typed ``httpx.AsyncClient`` wrapper. It never
imports the backend package; it speaks plain JSON over HTTP so team agents can
use it from a laptop with only the SDK installed.
"""

from __future__ import annotations

from typing import Any

import httpx

from agentlab.sdk.protocols import Event, HumanTask


class AgentLabError(Exception):
    """Raised for any non-success backend response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _error_message(response: httpx.Response) -> str:
    """Extract a human message from an error envelope, else the raw body."""
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("description"), str):
            return error["description"]
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
    return response.text or response.reason_phrase


class AgentLabClient:
    """Async wrapper around the backend REST API.

    ``agent_id`` is the registered identity sent as ``X-Agent-Id`` on
    agent-authenticated calls (currently ``emit_event``; SPEC §23).
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if agent_id:
            headers["X-Agent-Id"] = agent_id
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers)
        self.base_url = base_url
        self.agent_id = agent_id

    async def __aenter__(self) -> AgentLabClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise AgentLabError(response.status_code, _error_message(response))

    async def create_case(
        self,
        case_id: str,
        employee_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/cases",
            json={"case_id": case_id, "employee_id": employee_id, "context": context},
        )
        self._raise_for_status(response)
        return response.json()

    async def get_case(self, case_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/cases/{case_id}")
        self._raise_for_status(response)
        return response.json()

    async def create_task(self, task: HumanTask) -> dict[str, Any]:
        response = await self._client.post("/tasks", json=task.model_dump(mode="json"))
        self._raise_for_status(response)
        return response.json()

    async def emit_event(self, event: Event) -> dict[str, Any]:
        """Append ``event`` to the case trace via ``POST /events`` (SPEC §23).

        The server forces ``actor`` to the authenticated ``X-Agent-Id`` and
        sets ``ts`` itself, so the client-supplied values are advisory only;
        the Event model keeps them required so in-process producers still
        build complete records. Requires the client to be constructed with a
        registered ``agent_id``.
        """
        response = await self._client.post("/events", json=event.to_dict())
        self._raise_for_status(response)
        return response.json()

    async def list_events(self, case_id: str) -> list[dict[str, Any]]:
        response = await self._client.get(f"/cases/{case_id}/events")
        self._raise_for_status(response)
        return response.json()
