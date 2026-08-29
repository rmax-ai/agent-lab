"""Deterministic tests for the Device agent's MockWorld tools (SPEC §10).

Every tool is exercised against canned ``httpx.MockTransport`` responses: the
happy path, the ``NO_INVENTORY`` 409 rejection, a missing assignment, and a
transport-level failure. Assertions also check that the ``X-Agent-Id`` header
is always sent and that tools never raise.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from ..tools import device

ResponseHandler = Callable[[httpx.Request], httpx.Response]
InstallHandler = Callable[[ResponseHandler], None]


@pytest.fixture
def mock_world() -> Iterator[InstallHandler]:
    """Install a ``MockTransport`` for one test, then restore the module state."""
    original = device.TRANSPORT

    def install(handler: ResponseHandler) -> None:
        device.TRANSPORT = httpx.MockTransport(handler)

    try:
        yield install
    finally:
        device.TRANSPORT = original


async def test_get_employee_device_requirements(mock_world: InstallHandler) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["agent_id"] = request.headers.get("x-agent-id", "")
        path = request.url.path
        if path == "/world/employees/E42":
            return httpx.Response(
                200,
                json={"id": "E42", "role": "Software Engineer", "location": "Amsterdam"},
            )
        assert path == "/world/devices/E42"
        return httpx.Response(
            200,
            json={"required_sku": "macbook_pro_14", "assigned_device": None, "order": None},
        )

    mock_world(handler)
    result = await device.get_employee_device_requirements("E42")

    assert result["role"] == "Software Engineer"
    assert result["location"] == "Amsterdam"
    assert result["required_sku"] == "macbook_pro_14"
    assert result["assigned"] is None
    assert result["order"] is None
    assert captured["agent_id"] == "device-agent"


async def test_check_inventory(mock_world: InstallHandler) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/world/devices/inventory"
        return httpx.Response(
            200,
            json=[
                {"sku": "macbook_pro_14", "label": "MacBook Pro 14", "available": 1},
                {"sku": "macbook_air_15", "label": "MacBook Air 15", "available": 7},
            ],
        )

    mock_world(handler)
    result = await device.check_inventory("E42")

    assert len(result["inventory"]) == 2
    assert result["available"] == {"macbook_pro_14": 1, "macbook_air_15": 7}


async def test_get_device_assignment_missing(mock_world: InstallHandler) -> None:
    body = {"required_sku": "macbook_pro_14", "assigned_device": None, "order": None}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/world/devices/E42"
        return httpx.Response(200, json=body)

    mock_world(handler)
    result = await device.get_device_assignment("E42")

    assert result == body
    assert result["assigned_device"] is None


async def test_reserve_device_success(mock_world: InstallHandler) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = f"{request.method} {request.url.path}"
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={
                "device": {
                    "id": "DEV-1",
                    "employee_id": "E42",
                    "sku": "macbook_pro_14",
                    "status": "assigned",
                }
            },
        )

    mock_world(handler)
    result = await device.reserve_device("E42", "macbook_pro_14")

    assert captured["path"] == "POST /world/devices/E42/reserve"
    assert json.loads(captured["body"]) == {"sku": "macbook_pro_14"}
    assert result["reserved"] is True
    assert result["device"]["sku"] == "macbook_pro_14"


async def test_reserve_device_no_inventory(mock_world: InstallHandler) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"error": {"code": "NO_INVENTORY", "description": "Standard device unavailable"}},
        )

    mock_world(handler)
    result = await device.reserve_device("E42", "macbook_pro_14")

    assert result == {
        "reserved": False,
        "code": "NO_INVENTORY",
        "description": "Standard device unavailable",
    }


@pytest.mark.parametrize(
    ("device_body", "expected"),
    [
        (
            {
                "required_sku": "macbook_pro_14",
                "assigned_device": {
                    "id": "DEV-1",
                    "employee_id": "E42",
                    "sku": "macbook_pro_14",
                    "status": "assigned",
                },
                "order": None,
            },
            {"delivered": True, "status": "delivered", "eta": None},
        ),
        (
            {
                "required_sku": "macbook_pro_14",
                "assigned_device": {
                    "id": "DEV-1",
                    "employee_id": "E42",
                    "sku": "macbook_pro_14",
                    "status": "replacement_ordered",
                },
                "order": {
                    "id": "ORD-1",
                    "employee_id": "E42",
                    "sku": "macbook_pro_14",
                    "status": "ordered",
                    "eta": "2026-09-10",
                },
            },
            {"delivered": False, "status": "ordered", "eta": "2026-09-10"},
        ),
        (
            {"required_sku": "macbook_pro_14", "assigned_device": None, "order": None},
            {"delivered": False, "status": "none", "eta": None},
        ),
    ],
)
async def test_get_delivery_status(
    mock_world: InstallHandler,
    device_body: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/world/devices/E42"
        return httpx.Response(200, json=device_body)

    mock_world(handler)
    result = await device.get_delivery_status("E42")

    assert result == expected


async def test_request_replacement(mock_world: InstallHandler) -> None:
    captured: Any = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        assert request.method == "POST"
        assert request.url.path == "/world/devices/E42/replace"
        captured = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "order": {
                    "id": "ORD-1",
                    "employee_id": "E42",
                    "sku": "macbook_pro_14",
                    "status": "ordered",
                    "eta": None,
                }
            },
        )

    mock_world(handler)
    result = await device.request_replacement("E42", "defective screen")

    assert captured == {"reason": "defective screen"}
    assert result["order"]["id"] == "ORD-1"


async def test_sends_x_agent_id_header(mock_world: InstallHandler) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["agent_id"] = request.headers.get("x-agent-id", "")
        return httpx.Response(200, json=[])

    mock_world(handler)
    await device.check_inventory("E42")

    assert captured["agent_id"] == "device-agent"


async def test_tools_never_raise_on_network_error(mock_world: InstallHandler) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    mock_world(handler)
    result = await device.get_device_assignment("E42")

    assert result["error"]["code"] == "NETWORK_ERROR"


async def test_tools_never_raise_on_bad_response(mock_world: InstallHandler) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="not json")

    mock_world(handler)
    result = await device.get_delivery_status("E42")

    assert result == {"delivered": False, "status": "none", "eta": None}
