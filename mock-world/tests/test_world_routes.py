"""Route tests for MockWorld (SPEC §8, DEC-07, DEC-09).

Every request runs against ``create_app()`` in-process via ``httpx``
(``AsyncClient`` + ``ASGITransport``) with a temp ``AGENTLAB_DB``, so nothing
touches the repo's default database file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlmodel import select

from agentlab.world import app as world_app
from agentlab.world import db
from agentlab.world.app import create_app
from agentlab.world.models import ApplicationAccess

SIMULATOR_TOKEN = "test-sim-token"
_ALLOWED_DOMAINS = (
    "device-agent:devices,"
    "access-agent:access,"
    "systems-agent:systems,"
    "apps-agent:applications"
)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "world.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", SIMULATOR_TOKEN)
    monkeypatch.setenv("ALLOWED_DOMAINS", _ALLOWED_DOMAINS)
    world_app.ACTIVE_FAULTS.clear()
    db.reset_engine()
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _sim_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SIMULATOR_TOKEN}"}


async def test_employee_200_and_404(client: httpx.AsyncClient) -> None:
    ok = await client.get("/world/employees/E42", headers={"X-Agent-Id": "device-agent"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["id"] == "E42"
    assert body["name"] == "Eva Starter"
    assert body["manager_name"] == "Morgan Manager"

    missing = await client.get(
        "/world/employees/E999",
        headers={"X-Agent-Id": "device-agent"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


async def test_inventory_lists_seeded_skus(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/world/devices/inventory",
        headers={"X-Agent-Id": "device-agent"},
    )
    assert response.status_code == 200
    items = {item["sku"]: item for item in response.json()}
    assert items["macbook_pro_14"]["label"] == "MacBook Pro 14"
    assert items["macbook_pro_14"]["available"] == 1
    assert items["macbook_air_15"]["available"] == 7


async def test_reserve_happy_path_decrements_and_returns_device(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-Agent-Id": "device-agent"}
    response = await client.post(
        "/world/devices/E42/reserve",
        headers=headers,
        json={"sku": "macbook_pro_14"},
    )
    assert response.status_code == 201
    device = response.json()["device"]
    assert device["employee_id"] == "E42"
    assert device["sku"] == "macbook_pro_14"
    assert device["status"] == "assigned"

    inventory = await client.get("/world/devices/inventory", headers=headers)
    items = {item["sku"]: item for item in inventory.json()}
    assert items["macbook_pro_14"]["available"] == 0


async def test_reserve_no_inventory_409(client: httpx.AsyncClient) -> None:
    headers = {"X-Agent-Id": "device-agent"}
    first = await client.post(
        "/world/devices/E42/reserve",
        headers=headers,
        json={"sku": "macbook_pro_14"},
    )
    assert first.status_code == 201

    second = await client.post(
        "/world/devices/E42/reserve",
        headers=headers,
        json={"sku": "macbook_pro_14"},
    )
    assert second.status_code == 409
    assert second.json() == {
        "error": {
            "code": "NO_INVENTORY",
            "description": "Standard device unavailable",
        }
    }


async def test_replace_creates_order_and_flips_status(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-Agent-Id": "device-agent"}
    res = await client.post(
        "/world/devices/E42/reserve",
        headers=headers,
        json={"sku": "macbook_pro_14"},
    )
    assert res.status_code == 201

    response = await client.post(
        "/world/devices/E42/replace",
        headers=headers,
        json={"reason": "broken on arrival"},
    )
    assert response.status_code == 201
    order = response.json()["order"]
    assert order["employee_id"] == "E42"
    assert order["sku"] == "macbook_pro_14"
    assert order["status"] == "ordered"

    summary = await client.get("/world/devices/E42", headers=headers)
    assert summary.status_code == 200
    data = summary.json()
    assert data["required_sku"] == "macbook_pro_14"
    assert data["assigned_device"]["status"] == "replacement_ordered"
    assert data["order"]["id"] == order["id"]


async def test_access_summary_shape(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/world/access/E42",
        headers={"X-Agent-Id": "access-agent"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["username"] == "eva.starter"
    assert body["identity"]["status"] == "created"
    entitlement_groups = {e["group_id"]: e for e in body["entitlements"]}
    assert entitlement_groups["GRP-STANDARD"]["status"] == "granted"
    group_kinds = {g["id"]: g["kind"] for g in body["groups"]}
    assert group_kinds["GRP-STANDARD"] == "baseline"


async def test_request_creates_row_and_listing(client: httpx.AsyncClient) -> None:
    headers = {"X-Agent-Id": "access-agent"}
    created = await client.post(
        "/world/access/E42/request",
        headers=headers,
        json={"group_id": "GRP-PRIVILEGED", "description": "prod access"},
    )
    assert created.status_code == 201
    assert created.json()["request"]["status"] == "requested"

    listing = await client.get("/world/access/E42/requests", headers=headers)
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["group_id"] == "GRP-PRIVILEGED"


async def test_systems_listing_with_missing_account_status(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/world/systems/E42",
        headers={"X-Agent-Id": "systems-agent"},
    )
    assert response.status_code == 200
    rows = {row["system_id"]: row for row in response.json()}
    assert set(rows) == {"SYS-EMAIL", "SYS-VPN", "SYS-HR"}
    assert all(row["account_status"] == "missing" for row in rows.values())


async def test_provision_idempotent_single_row(client: httpx.AsyncClient) -> None:
    headers = {"X-Agent-Id": "apps-agent"}
    first = await client.post(
        "/world/applications/E42/provision",
        headers=headers,
        json={"application_id": "APP-GITHUB"},
    )
    assert first.status_code == 201

    second = await client.post(
        "/world/applications/E42/provision",
        headers=headers,
        json={"application_id": "APP-GITHUB"},
    )
    assert second.status_code == 201

    listing = await client.get("/world/applications/E42", headers=headers)
    granted = {row["application_id"]: row["granted"] for row in listing.json()}
    assert granted["APP-GITHUB"] is True
    assert granted["APP-SLACK"] is True
    assert granted["APP-GOOGLE-WORKSPACE"] is True

    with db.session_scope() as session:
        count = len(
            session.exec(
                select(ApplicationAccess).where(
                    ApplicationAccess.employee_id == "E42",
                    ApplicationAccess.application_id == "APP-GITHUB",
                )
            ).all()
        )
    assert count == 1


async def test_cross_domain_rejection(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/world/access/E42",
        headers={"X-Agent-Id": "device-agent"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_missing_agent_id_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/world/devices/inventory")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_simulation_mutate_changes_world(client: httpx.AsyncClient) -> None:
    mutated = await client.post(
        "/simulation/mutate",
        headers=_sim_headers(),
        json={"path": "inventory.macbook_pro_14.available", "value": 0},
    )
    assert mutated.status_code == 200
    assert mutated.json() == {"path": "inventory.macbook_pro_14.available", "value": 0}

    inventory = await client.get(
        "/world/devices/inventory",
        headers={"X-Agent-Id": "device-agent"},
    )
    items = {item["sku"]: item for item in inventory.json()}
    assert items["macbook_pro_14"]["available"] == 0


async def test_simulation_mutate_unknown_path_404(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/simulation/mutate",
        headers=_sim_headers(),
        json={"path": "bogus.collection.field", "value": 1},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_simulation_routes_require_bearer(client: httpx.AsyncClient) -> None:
    missing = await client.post("/simulation/reset")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "UNAUTHORIZED"

    wrong = await client.post(
        "/simulation/reset",
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert wrong.status_code == 401


async def test_reset_reseeds_canonical_counts(client: httpx.AsyncClient) -> None:
    headers = {"X-Agent-Id": "device-agent"}
    await client.post(
        "/world/devices/E42/reserve",
        headers=headers,
        json={"sku": "macbook_pro_14"},
    )

    reset = await client.post("/simulation/reset", headers=_sim_headers())
    assert reset.status_code == 200
    assert reset.json() == {"status": "reset"}

    inventory = await client.get("/world/devices/inventory", headers=headers)
    items = {item["sku"]: item for item in inventory.json()}
    assert items["macbook_pro_14"]["available"] == 1
    assert items["macbook_air_15"]["available"] == 7

    employee = await client.get("/world/employees/E42", headers=headers)
    assert employee.status_code == 200


async def test_load_resets_then_applies_flat_state(client: httpx.AsyncClient) -> None:
    headers = {"X-Agent-Id": "device-agent"}

    # Dirty the canonical state: assign a device and mutate an employee field.
    await client.post(
        "/world/devices/E42/reserve",
        headers=headers,
        json={"sku": "macbook_pro_14"},
    )
    await client.post(
        "/simulation/mutate",
        headers=_sim_headers(),
        json={"path": "employees.E42.status", "value": "terminated"},
    )

    response = await client.post(
        "/simulation/load",
        headers=_sim_headers(),
        json={"state": {"inventory.macbook_air_15.available": 3}},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "loaded"}

    # Reset restored untouched state; the one provided mutation was applied.
    inventory = await client.get("/world/devices/inventory", headers=headers)
    items = {item["sku"]: item for item in inventory.json()}
    assert items["macbook_pro_14"]["available"] == 1
    assert items["macbook_air_15"]["available"] == 3

    employee = await client.get("/world/employees/E42", headers=headers)
    assert employee.json()["status"] == "pending"

    device_summary = await client.get("/world/devices/E42", headers=headers)
    assert device_summary.json()["assigned_device"] is None


async def test_faults_arm_and_list(client: httpx.AsyncClient) -> None:
    armed = await client.post(
        "/simulation/faults",
        headers=_sim_headers(),
        json={"tool": "reserve_device", "fault": "timeout"},
    )
    assert armed.status_code == 200
    assert armed.json() == {"status": "armed"}

    listing = await client.get("/simulation/faults", headers=_sim_headers())
    assert listing.status_code == 200
    assert {"tool": "reserve_device", "fault": "timeout"} in listing.json()


async def test_events_not_implemented(client: httpx.AsyncClient) -> None:
    response = await client.post("/simulation/events", headers=_sim_headers())
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
