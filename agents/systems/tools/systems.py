"""Systems-domain ADK function tools (SPEC §10).

Each tool is a plain async function whose docstring becomes the ADK tool
description. The tools talk to MockWorld over ``httpx`` with:

* base URL ``MOCKWORLD_URL`` (default ``http://localhost:8000``);
* header ``X-Agent-Id: systems-agent``;
* a 10 second timeout.

The systems world surface is READ-ONLY by design: MockWorld exposes only
``GET /world/systems/{employee_id}`` for this domain. There is no provisioning
route, so ``provision_account`` never touches the world — it opens a HumanTask
(an IT ticket) through the backend task flow at ``BACKEND_URL`` (default
``http://localhost:8001``) and account materialization is discovered later
through the truthful read tools.

Tools return flat, JSON-serialisable ``dict`` values. They never raise and
never leak raw HTTP errors: transport failures and non-JSON/business errors
are translated into structured ``{"error": {"code", "description"}}`` shapes
the agent can reason about. They only touch the ``/world/systems/*`` and
shared ``/world/employees/*`` routes plus the backend ``/tasks`` flow — never
``/simulation/*`` and never another domain's routes.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

MOCKWORLD_URL = os.environ.get("MOCKWORLD_URL", "http://localhost:8000").rstrip("/")
BACKEND_URL = os.environ.get("AGENTLAB_BACKEND_URL", "http://localhost:8001").rstrip("/")
AGENT_ID = "systems-agent"
_TIMEOUT = httpx.Timeout(10.0)

# The IT actor a provisioning HumanTask is addressed to (DEC-10: only this
# actor may resolve it). Placeholder identity, like the M1 manager seed.
IT_ACTOR = "it-support"

# Baseline systems every employee needs; SYS-HR is required for people
# managers ONLY (knowledge/systems/baseline-systems.md, hr-system-policy.md).
BASELINE_SYSTEMS = ["SYS-EMAIL", "SYS-VPN"]
HR_SYSTEM = "SYS-HR"

# Tests inject ``httpx.MockTransport``/ASGI transports here to intercept every
# HTTP call (world reads and the backend task flow respectively).
TRANSPORT: httpx.AsyncBaseTransport | None = None
BACKEND_TRANSPORT: httpx.AsyncBaseTransport | None = None


def _world_client() -> httpx.AsyncClient:
    """Build an async MockWorld client with the domain identity and timeout."""
    return httpx.AsyncClient(
        base_url=MOCKWORLD_URL,
        headers={"X-Agent-Id": AGENT_ID},
        timeout=_TIMEOUT,
        transport=TRANSPORT,
    )


def _backend_client() -> httpx.AsyncClient:
    """Build an async backend client with the domain identity and timeout."""
    return httpx.AsyncClient(
        base_url=BACKEND_URL,
        headers={"X-Agent-Id": AGENT_ID},
        timeout=_TIMEOUT,
        transport=BACKEND_TRANSPORT,
    )


async def _request(
    client_factory: Any,
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Send an HTTP request and return ``(status, body)``.

    ``body`` is the parsed JSON (a ``dict`` or ``list``). Transport-level
    failures return ``(0, {"error": {"code": "NETWORK_ERROR", ...}})`` and a
    non-JSON response returns a ``{"error": {"code": "BAD_RESPONSE", ...}}``
    body, so callers never see an exception or a raw response object.
    """
    try:
        async with client_factory() as client:
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


def _is_people_manager(role: Any) -> bool:
    """True when the employee's role makes them a people manager.

    People-manager roles carry "manager" in the role title (for example
    "Engineering Manager"). The canonical seed E42 is a Software Engineer and
    is NOT a people manager (knowledge/systems/hr-system-policy.md).
    """
    return isinstance(role, str) and "manager" in role.casefold()


async def get_required_systems(employee_id: str) -> dict[str, Any]:
    """Return the systems the employee is required to have accounts on.

    Combines the baseline policy (SYS-EMAIL and SYS-VPN for every employee)
    with the employee's role from the shared ``GET /world/employees`` route:
    SYS-HR is required ONLY for people managers. Use this first to learn the
    required set before checking account status.
    """
    _, body = await _request(_world_client, "GET", f"/world/employees/{employee_id}")
    if not isinstance(body, dict):
        return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}
    if "error" in body:
        return body
    role = body.get("role")
    hr_required = _is_people_manager(role)
    required = list(BASELINE_SYSTEMS)
    if hr_required:
        required.append(HR_SYSTEM)
    return {
        "employee_id": employee_id,
        "role": role,
        "manager_id": body.get("manager_id"),
        "required_systems": required,
        "hr_required": hr_required,
    }


async def get_account_status(employee_id: str) -> dict[str, Any]:
    """Return the employee's account status on every system in the catalog.

    Truthful read over ``GET /world/systems/{employee_id}``: one entry per
    system (SYS-EMAIL, SYS-VPN, SYS-HR) with ``account_status`` ``missing``
    (no account row exists), ``pending`` (provisioning in flight), or
    ``active``. This is the ONLY systems read the world offers — the systems
    surface is read-only.
    """
    _, body = await _request(_world_client, "GET", f"/world/systems/{employee_id}")
    if isinstance(body, list):
        return {"employee_id": employee_id, "accounts": body}
    if isinstance(body, dict):
        return {"employee_id": employee_id, "accounts": [], **body}
    return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}


async def provision_account(
    employee_id: str,
    system_id: str,
    case_id: str | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """Open an IT provisioning HumanTask for ``system_id`` on behalf of the employee.

    The systems world surface is READ-ONLY: MockWorld has NO provisioning
    route, and this tool must never fabricate one. Provisioning is a human
    (IT) action, so this tool opens a HumanTask through the REAL backend task
    flow (``POST /tasks``), addressed to the IT provisioning queue
    (``requested_from: it-support`` — DEC-10: only that actor may resolve it).

    Returns ``{"task_opened": true, "task": {...}}`` — a task REFERENCE, never
    a fake provisioning success. The account materializes later as a world
    state change performed by IT; discover it only through
    ``get_account_status`` / ``verify_account`` truthful reads. ``case_id``
    and ``workflow_id`` correlate the task with the run (SPEC §19); they
    default to the conventional onboarding ids for the employee.
    """
    case = case_id or f"ONB-{employee_id}"
    payload: dict[str, Any] = {
        "human_task_id": f"HT-{case}-{system_id}",
        "case_id": case,
        "workflow_id": workflow_id or "",
        "requested_by": AGENT_ID,
        "requested_from": IT_ACTOR,
        "type": "MANUAL_ACTION",
        "context": {
            "reason": "systems_account_provisioning",
            "employee_id": employee_id,
            "system_id": system_id,
            "policy": "it-provisioning-via-task",
        },
        "allowed_actions": ["approve", "reject"],
        "status": "open",
        "created_at": datetime.now(UTC).isoformat(),
    }
    _, body = await _request(_backend_client, "POST", "/tasks", json=payload)
    if isinstance(body, dict) and "human_task_id" in body and "error" not in body:
        return {"task_opened": True, "task": body}
    return {
        "task_opened": False,
        "code": _blocker_code(body),
        "description": _blocker_description(body, fallback="Provisioning task creation failed"),
    }


async def verify_account(employee_id: str) -> dict[str, Any]:
    """Verify the employee's accounts against the required set, truthfully.

    Reads the required systems (role-aware) and the world's account list, then
    reports per-system verification: a system is verified ONLY when its
    account row exists and is ``active`` — never when the row is absent
    (``missing``) or still ``pending``. An SYS-HR account for a non-manager is
    a policy violation (hr-system-policy): it is reported in
    ``policy_violations`` and NEVER counts as verified. Only report the
    workflow complete when ``all_required_verified`` is true and
    ``policy_violations`` is empty.
    """
    required = await get_required_systems(employee_id)
    if "error" in required:
        return {
            "employee_id": employee_id,
            "verified": {},
            "all_required_verified": False,
            "missing_required": [],
            "pending_required": [],
            "policy_violations": [],
            "error": required["error"],
        }
    status = await get_account_status(employee_id)
    if "error" in status:
        return {
            "employee_id": employee_id,
            "verified": {},
            "all_required_verified": False,
            "missing_required": [],
            "pending_required": [],
            "policy_violations": [],
            "error": status["error"],
        }

    accounts = {
        row["system_id"]: row["account_status"]
        for row in status["accounts"]
        if isinstance(row, dict) and "system_id" in row
    }
    required_systems: list[str] = required["required_systems"]
    hr_required: bool = required["hr_required"]

    verified: dict[str, bool] = {}
    for system_id, account_status in accounts.items():
        if system_id == HR_SYSTEM and not hr_required:
            # An HR account for a non-manager can never be "verified" — it is
            # a policy violation regardless of its status.
            verified[system_id] = False
        else:
            verified[system_id] = account_status == "active"

    missing_required = [s for s in required_systems if accounts.get(s) == "missing"]
    pending_required = [
        s for s in required_systems if accounts.get(s) not in (None, "missing", "active")
    ]
    policy_violations: list[str] = []
    if not hr_required and accounts.get(HR_SYSTEM, "missing") != "missing":
        policy_violations.append("hr_account_for_non_manager")

    return {
        "employee_id": employee_id,
        "role": required["role"],
        "required_systems": required_systems,
        "accounts": accounts,
        "verified": verified,
        "all_required_verified": all(verified.get(s) is True for s in required_systems),
        "missing_required": missing_required,
        "pending_required": pending_required,
        "policy_violations": policy_violations,
    }


__all__ = [
    "get_account_status",
    "get_required_systems",
    "provision_account",
    "verify_account",
]
