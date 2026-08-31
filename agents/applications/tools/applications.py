"""Applications-domain ADK function tools (SPEC §10).

Each tool is a plain async function whose docstring becomes the ADK tool
description. The tools talk to MockWorld over ``httpx`` with:

* base URL ``MOCKWORLD_URL`` (default ``http://localhost:8000``);
* header ``X-Agent-Id: applications-agent``;
* a 10 second timeout.

Applications is a FULL MUTATOR domain: MockWorld exposes the truthful read
``GET /world/applications/{employee_id}`` AND the idempotent provisioning
route ``POST /world/applications/{employee_id}/provision`` (SPEC §8). There
is NO revoke route — provisioning only ever grants, and re-granting an
already-granted application is safe (the world re-marks the same row
``granted``).

Tools return flat, JSON-serialisable ``dict`` values. They never raise and
never leak raw HTTP errors: transport failures and non-JSON/business errors
are translated into structured ``{"error": {"code", "description"}}`` shapes
the agent can reason about. They only touch the ``/world/applications/*``
and shared ``/world/employees/*`` routes — never ``/simulation/*`` and never
another domain's routes.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

MOCKWORLD_URL = os.environ.get("MOCKWORLD_URL", "http://localhost:8000").rstrip("/")
AGENT_ID = "applications-agent"
_TIMEOUT = httpx.Timeout(10.0)

# The role→application mapping (knowledge/applications/role-application-mapping.md
# is the human-readable single source of truth; these constants are its
# executable form): Slack + Google Workspace for EVERY employee; GitHub ONLY
# for engineering roles.
BASELINE_APPLICATIONS = ["APP-SLACK", "APP-GOOGLE-WORKSPACE"]
ENGINEERING_APPLICATION = "APP-GITHUB"

# Tests inject a ``httpx.MockTransport``/ASGI transport here to intercept
# every HTTP call.
TRANSPORT: httpx.AsyncBaseTransport | None = None


def _client() -> httpx.AsyncClient:
    """Build an async MockWorld client with the domain identity and timeout."""
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


def _is_engineering_role(role: Any) -> bool:
    """True when the employee's role is an engineering role.

    Engineering roles carry "engineer" in the role title (for example
    "Software Engineer"). Only engineering roles require APP-GITHUB
    (knowledge/applications/role-application-mapping.md).
    """
    return isinstance(role, str) and "engineer" in role.casefold()


async def get_required_applications(employee_id: str) -> dict[str, Any]:
    """Return the applications the employee is required to hold.

    Combines the role→application mapping policy (APP-SLACK and
    APP-GOOGLE-WORKSPACE for every employee; APP-GITHUB ONLY for engineering
    roles — see knowledge/applications/role-application-mapping.md) with the
    employee's role from the shared ``GET /world/employees`` route. Use this
    first to learn the required set before checking or provisioning access.
    """
    _, body = await _request("GET", f"/world/employees/{employee_id}")
    if not isinstance(body, dict):
        return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}
    if "error" in body:
        return body
    role = body.get("role")
    github_required = _is_engineering_role(role)
    required = list(BASELINE_APPLICATIONS)
    if github_required:
        required.append(ENGINEERING_APPLICATION)
    return {
        "employee_id": employee_id,
        "role": role,
        "manager_id": body.get("manager_id"),
        "required_applications": required,
        "github_required": github_required,
    }


async def get_application_access(employee_id: str) -> dict[str, Any]:
    """Return the employee's grant state for every application in the catalog.

    Truthful read over ``GET /world/applications/{employee_id}``: one entry
    per catalog application (APP-SLACK, APP-GOOGLE-WORKSPACE, APP-GITHUB)
    with ``granted`` true/false. This is the authoritative read used before
    provisioning (provision only what is required AND missing) and for
    verification before reporting the workflow complete.
    """
    _, body = await _request("GET", f"/world/applications/{employee_id}")
    if isinstance(body, list):
        return {"employee_id": employee_id, "applications": body}
    if isinstance(body, dict):
        return {"employee_id": employee_id, "applications": [], **body}
    return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}


async def provision_application(employee_id: str, application_id: str) -> dict[str, Any]:
    """Grant ``application_id`` to the employee (idempotently).

    Calls the world's provisioning route
    ``POST /world/applications/{employee_id}/provision``. The route is
    idempotent: re-granting an already-granted application simply re-marks
    the same row ``granted`` — never an error, never a duplicate.

    Returns ``{"provisioned": true, "application_access": {...}}`` on success
    (HTTP 201, status ``granted``). An application id absent from the world
    catalog returns ``{"provisioned": false, "code": "NOT_FOUND", ...}`` —
    surfaced honestly, never retried with a guessed id (see
    knowledge/applications/unknown-applications.md). Provision ONLY what the
    role→application mapping requires; there is no revoke route, so a wrong
    grant cannot be undone.
    """
    _, body = await _request(
        "POST",
        f"/world/applications/{employee_id}/provision",
        json={"application_id": application_id},
    )
    if isinstance(body, dict) and "application_access" in body:
        return {"provisioned": True, "application_access": body["application_access"]}
    return {
        "provisioned": False,
        "code": _blocker_code(body),
        "description": _blocker_description(body, fallback="Application provisioning failed"),
    }


async def verify_application_access(employee_id: str) -> dict[str, Any]:
    """Verify the employee's application grants against the required set.

    Truthful verification over ``get_required_applications`` and
    ``get_application_access``: a required application is verified ONLY when
    the world reports it ``granted``. An APP-GITHUB grant for a
    non-engineering role is a policy violation
    (knowledge/applications/role-application-mapping.md): it is reported in
    ``policy_violations`` and NEVER counts as verified. Only report the
    workflow complete when ``all_required_verified`` is true and
    ``policy_violations`` is empty.
    """
    required = await get_required_applications(employee_id)
    if "error" in required:
        return {
            "employee_id": employee_id,
            "verified": {},
            "all_required_verified": False,
            "missing_required": [],
            "policy_violations": [],
            "error": required["error"],
        }
    access = await get_application_access(employee_id)
    if "error" in access:
        return {
            "employee_id": employee_id,
            "verified": {},
            "all_required_verified": False,
            "missing_required": [],
            "policy_violations": [],
            "error": access["error"],
        }

    grants = {
        row["application_id"]: bool(row.get("granted"))
        for row in access["applications"]
        if isinstance(row, dict) and "application_id" in row
    }
    required_applications: list[str] = required["required_applications"]
    github_required: bool = required["github_required"]

    verified: dict[str, bool] = {}
    for application_id, granted in grants.items():
        if application_id == ENGINEERING_APPLICATION and not github_required:
            # A GitHub grant for a non-engineering role can never be
            # "verified" — it is a policy violation regardless of its state.
            verified[application_id] = False
        else:
            verified[application_id] = granted

    missing_required = [a for a in required_applications if not grants.get(a, False)]
    policy_violations: list[str] = []
    if not github_required and grants.get(ENGINEERING_APPLICATION, False):
        policy_violations.append("out_of_role_application_granted")

    return {
        "employee_id": employee_id,
        "role": required["role"],
        "required_applications": required_applications,
        "grants": grants,
        "verified": verified,
        "all_required_verified": all(
            verified.get(a) is True for a in required_applications
        ),
        "missing_required": missing_required,
        "policy_violations": policy_violations,
    }


__all__ = [
    "get_application_access",
    "get_required_applications",
    "provision_application",
    "verify_application_access",
]
