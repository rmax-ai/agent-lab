"""Access-domain ADK function tools (SPEC §10).

Each tool is a plain async function whose docstring becomes the ADK tool
description. All three talk to MockWorld over ``httpx`` with:

* base URL ``MOCKWORLD_URL`` (default ``http://localhost:8000``);
* header ``X-Agent-Id: access-agent``;
* a 10 second timeout.

Tools return flat, JSON-serialisable ``dict`` values. They never raise and
never leak raw HTTP errors: transport failures and non-JSON/business errors
are translated into structured ``{"error": {"code", "description"}}`` shapes
the agent can reason about. They only touch the ``/world/access/*`` routes —
never ``/simulation/*`` and never another domain's routes.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

MOCKWORLD_URL = os.environ.get("MOCKWORLD_URL", "http://localhost:8000").rstrip("/")
AGENT_ID = "access-agent"
_TIMEOUT = httpx.Timeout(10.0)

# Tests inject a ``httpx.MockTransport``/ASGI transport here to intercept
# every HTTP call.
TRANSPORT: httpx.AsyncBaseTransport | None = None


def _client() -> httpx.AsyncClient:
    """Build an async HTTP client with the domain identity and timeout."""
    return httpx.AsyncClient(
        base_url=MOCKWORLD_URL,
        headers={"X-Agent-Id": AGENT_ID},
        timeout=_TIMEOUT,
        transport=TRANSPORT,
    )


async def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Send an HTTP request to MockWorld and return ``(status, body)``.

    ``body`` is the parsed JSON (a ``dict`` or ``list``). Transport-level
    failures return ``(0, {"error": {"code": "NETWORK_ERROR", ...}})`` and a
    non-JSON response returns a ``{"error": {"code": "BAD_RESPONSE", ...}}``
    body, so callers never see an exception or a raw response object.
    """
    try:
        async with _client() as client:
            response = await client.request(method, path, json=json)
    except httpx.HTTPError as exc:
        return 0, {"error": {"code": "NETWORK_ERROR", "description": str(exc)}}
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {
            "error": {
                "code": "BAD_RESPONSE",
                "description": response.text.strip() or f"HTTP {response.status_code}",
            }
        }


def _blocker_code(body: Any) -> str:
    """Extract a human-readable rejection code from an error envelope."""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str):
            return code
    return "HTTP_ERROR"


def _blocker_description(body: Any, *, fallback: str) -> str:
    """Extract a rejection description, falling back when the body is unexpected."""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        description = error.get("description")
        if isinstance(description, str):
            return description
    return fallback


async def get_access_summary(employee_id: str) -> dict[str, Any]:
    """Return the employee's identity, entitlements, and the groups behind them.

    Use this first to learn what access the employee already holds. An
    employee unknown to the world returns ``identity: null`` with empty
    ``entitlements`` and ``groups`` — treat that as not-found and ask a human
    rather than guessing.
    """
    _, body = await _request("GET", f"/world/access/{employee_id}")
    if isinstance(body, dict):
        return body
    return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}


async def request_group_access(
    employee_id: str,
    group_id: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Create an access request for ``group_id`` on behalf of the employee.

    Returns ``{"requested": true, "request": {...}}`` on success (HTTP 201,
    status ``requested``). A business or validation rejection is returned as
    ``{"requested": false, "code": ..., "description": ...}`` and never
    raises. Privileged groups require manager approval BEFORE this call — see
    the privileged-group-approvals policy.
    """
    payload: dict[str, Any] = {"group_id": group_id}
    if description is not None:
        payload["description"] = description
    _, body = await _request("POST", f"/world/access/{employee_id}/request", json=payload)
    if isinstance(body, dict) and "request" in body:
        return {"requested": True, "request": body["request"]}
    return {
        "requested": False,
        "code": _blocker_code(body),
        "description": _blocker_description(body, fallback="Access request failed"),
    }


async def list_access_requests(employee_id: str) -> dict[str, Any]:
    """List the employee's access requests, newest world state first-hand.

    This is the authoritative read used to track a request's status
    (``requested`` → ``granted``/``denied``) and to verify the workflow
    outcome before reporting it complete.
    """
    _, body = await _request("GET", f"/world/access/{employee_id}/requests")
    if isinstance(body, list):
        return {"requests": body}
    if isinstance(body, dict):
        return {"requests": [], **body}
    return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}


__all__ = [
    "get_access_summary",
    "list_access_requests",
    "request_group_access",
]
