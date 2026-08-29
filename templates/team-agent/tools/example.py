"""Example ADK function tool for a team agent.

This shows the MockWorld HTTP pattern every domain tool follows. Tools must be
plain async functions with a docstring (the docstring becomes the tool
description), return flat dicts, and never raise or leak raw HTTP errors.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

# TODO: replace with your team's registered agent id (must match the id used by
# the transport and the ALLOWED_DOMAINS entries in the lab's MockWorld config).
AGENT_ID = os.environ.get("AGENTLAB_AGENT_ID", "team-agent")
MOCKWORLD_URL = os.environ.get("MOCKWORLD_URL", "http://localhost:8000").rstrip("/")
_TIMEOUT = httpx.Timeout(10.0)


async def example_get_employee(employee_id: str) -> dict[str, Any]:
    """Return basic person data for ``employee_id`` from the shared endpoint.

    Every registered agent may call ``/world/employees/{id}``. Domain-specific
    routes are domain-enforced server-side, so a team agent can only reach the
    ``/world/*`` routes for its own registered domain.
    """
    async with httpx.AsyncClient(
        base_url=MOCKWORLD_URL,
        headers={"X-Agent-Id": AGENT_ID},
        timeout=_TIMEOUT,
    ) as client:
        response = await client.get(f"/world/employees/{employee_id}")

    try:
        body: Any = response.json()
    except ValueError:
        return {"error": {"code": "BAD_RESPONSE", "description": response.text.strip()}}

    if isinstance(body, dict):
        return body
    return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}
