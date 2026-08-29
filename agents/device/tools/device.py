"""Device-domain ADK function tools (SPEC §10).

Each tool is a plain async function whose docstring becomes the ADK tool
description. All six talk to MockWorld over ``httpx`` with:

* base URL ``MOCKWORLD_URL`` (default ``http://localhost:8000``);
* header ``X-Agent-Id: device-agent``;
* a 10 second timeout.

Tools return flat, JSON-serialisable ``dict`` values. They never raise and
never leak raw HTTP errors: transport failures and non-JSON/business errors are
translated into structured ``{"error": {"code", "description"}}`` shapes the
agent can reason about.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

MOCKWORLD_URL = os.environ.get("MOCKWORLD_URL", "http://localhost:8000").rstrip("/")
AGENT_ID = "device-agent"
_TIMEOUT = httpx.Timeout(10.0)

# Tests inject a ``httpx.MockTransport`` here to intercept every HTTP call.
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


async def get_employee_device_requirements(employee_id: str) -> dict[str, Any]:
    """Return a single onboarding snapshot for the employee.

    Combines the HR employee record (role, location) with the device summary
    (required SKU, current assigned device, open order). Use this first to
    learn what the employee needs and what has already happened.
    """
    _, employee = await _request("GET", f"/world/employees/{employee_id}")
    _, device = await _request("GET", f"/world/devices/{employee_id}")
    employee_body = employee if isinstance(employee, dict) else {}
    device_body = device if isinstance(device, dict) else {}

    result: dict[str, Any] = {
        "role": employee_body.get("role"),
        "location": employee_body.get("location"),
        "required_sku": device_body.get("required_sku"),
        "assigned": device_body.get("assigned_device"),
        "order": device_body.get("order"),
    }
    if "error" in employee_body:
        result["error"] = employee_body["error"]
    elif "error" in device_body:
        result["error"] = device_body["error"]
    return result


async def check_inventory(employee_id: str) -> dict[str, Any]:
    """Check current device inventory and summarise availability by SKU.

    ``employee_id`` is accepted for a uniform tool interface; inventory is
    shared across the device domain, so the returned list is global. The
    ``available`` summary maps each SKU to its remaining stock.
    """
    status, body = await _request("GET", "/world/devices/inventory")
    if status == 0 or not isinstance(body, list):
        error = body if isinstance(body, dict) else {}
        return {"inventory": [], "available": {}, **error}

    inventory = body
    available: dict[str, int] = {}
    for item in inventory:
        if not isinstance(item, dict):
            continue
        sku = item.get("sku")
        count = item.get("available", 0)
        if isinstance(sku, str):
            available[sku] = count if isinstance(count, int) else 0
    return {"inventory": inventory, "available": available}


async def get_device_assignment(employee_id: str) -> dict[str, Any]:
    """Return the employee's required SKU, assigned device, and current order.

    This is the authoritative read used to verify the workflow outcome before
    reporting it complete.
    """
    _, body = await _request("GET", f"/world/devices/{employee_id}")
    if isinstance(body, dict):
        return body
    return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}


async def reserve_device(employee_id: str, sku: str) -> dict[str, Any]:
    """Reserve ``sku`` for ``employee_id``, decrementing inventory.

    Returns ``{"reserved": true, "device": {...}}`` on success. A business
    rejection such as ``NO_INVENTORY`` is returned as
    ``{"reserved": false, "code": ..., "description": ...}`` and never raises.
    """
    _, body = await _request(
        "POST",
        f"/world/devices/{employee_id}/reserve",
        json={"sku": sku},
    )
    if isinstance(body, dict) and "device" in body:
        return {"reserved": True, "device": body["device"]}
    return {
        "reserved": False,
        "code": _blocker_code(body),
        "description": _blocker_description(body, fallback="Reservation failed"),
    }


async def get_delivery_status(employee_id: str) -> dict[str, Any]:
    """Derive delivery state from the employee's assignment and order.

    Returns ``delivered`` (bool), ``status`` ("ordered", "delivered", or
    "none"), and an optional ``eta``. An assigned device with no open order is
    treated as delivered; an open order means "ordered".
    """
    _, body = await _request("GET", f"/world/devices/{employee_id}")
    if not isinstance(body, dict) or "error" in body:
        return {"delivered": False, "status": "none", "eta": None}

    order = body.get("order")
    if isinstance(order, dict):
        eta_value = order.get("eta")
        eta = eta_value if isinstance(eta_value, str) else None
        return {"delivered": False, "status": "ordered", "eta": eta}

    if body.get("assigned_device") is not None:
        return {"delivered": True, "status": "delivered", "eta": None}

    return {"delivered": False, "status": "none", "eta": None}


async def request_replacement(employee_id: str, reason: str) -> dict[str, Any]:
    """Order a replacement for the employee's assigned device.

    ``reason`` describes the defect or wrong-delivery issue. Returns the new
    order (``{"order": {...}}``) or a structured error, never raising.
    """
    _, body = await _request(
        "POST",
        f"/world/devices/{employee_id}/replace",
        json={"reason": reason},
    )
    if isinstance(body, dict):
        return body
    return {"error": {"code": "BAD_RESPONSE", "description": "Unexpected response shape"}}


__all__ = [
    "check_inventory",
    "get_delivery_status",
    "get_device_assignment",
    "get_employee_device_requirements",
    "request_replacement",
    "reserve_device",
]
