"""Case store route tests (SPEC §11, §19)."""

from __future__ import annotations

import httpx

COORDINATOR = "onboarding-agent"


async def test_create_list_and_get_case(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/cases",
        json={"case_id": "ONB-1", "employee_id": "E42", "context": {"start_date": "2026-09-07"}},
    )
    assert created.status_code == 201
    assert created.json()["case_id"] == "ONB-1"
    assert created.json()["employee_id"] == "E42"
    assert created.json()["status"] == "open"

    listing = await client.get("/cases")
    assert listing.status_code == 200
    summaries = listing.json()
    assert len(summaries) == 1
    assert summaries[0]["case_id"] == "ONB-1"
    assert summaries[0]["employee_id"] == "E42"
    assert summaries[0]["blockers"] == 0
    assert summaries[0]["open_approvals"] == 0

    detail = await client.get("/cases/ONB-1")
    assert detail.status_code == 200
    body = detail.json()
    assert body["case_id"] == "ONB-1"
    assert body["context"] == {"start_date": "2026-09-07"}
    assert body["domain_status"] == {}


async def test_domain_status_aggregation(client: httpx.AsyncClient) -> None:
    await client.post(
        "/cases",
        json={"case_id": "ONB-2", "employee_id": "E42", "context": {}},
    )

    for workflow_id, target, goal in (
        ("WF-D", "device-agent", "employee_device_ready"),
        ("WF-A", "access-agent", "employee_access_ready"),
    ):
        started = await client.post(
            "/workflows",
            headers={"X-Agent-Id": COORDINATOR},
            json={
                "workflow_id": workflow_id,
                "case_id": "ONB-2",
                "goal": goal,
                "employee_id": "E42",
                "context": {},
                "target_agent_id": target,
            },
        )
        assert started.status_code == 201
        acked = await client.post(
            f"/workflows/{workflow_id}/ack",
            headers={"X-Agent-Id": target},
        )
        assert acked.status_code == 200

    blocked = await client.post(
        "/workflows/WF-D/status",
        headers={"X-Agent-Id": "device-agent"},
        json={
            "workflow_id": "WF-D",
            "status": "blocked",
            "blockers": [{"code": "NO_INVENTORY", "description": "Standard device unavailable"}],
        },
    )
    assert blocked.status_code == 200

    detail = await client.get("/cases/ONB-2")
    assert detail.status_code == 200
    domain_status = detail.json()["domain_status"]
    assert domain_status["device"] == "blocked"
    assert domain_status["access"] == "running"

    listing = await client.get("/cases")
    summaries = {summary["case_id"]: summary for summary in listing.json()}
    assert summaries["ONB-2"]["blockers"] == 1


async def test_events_listing_ordered(client: httpx.AsyncClient) -> None:
    await client.post(
        "/cases",
        json={"case_id": "ONB-3", "employee_id": "E42", "context": {}},
    )
    await client.post(
        "/workflows",
        headers={"X-Agent-Id": COORDINATOR},
        json={
            "workflow_id": "WF-3",
            "case_id": "ONB-3",
            "goal": "employee_device_ready",
            "employee_id": "E42",
            "context": {},
            "target_agent_id": "device-agent",
        },
    )
    await client.post(
        "/workflows/WF-3/ack",
        headers={"X-Agent-Id": "device-agent"},
    )

    events = (await client.get("/cases/ONB-3/events")).json()["events"]
    assert [event["type"] for event in events] == [
        "CASE_CREATED",
        "WORKFLOW_DELEGATED",
        "WORKFLOW_ACKNOWLEDGED",
    ]
    assert [event["case_id"] for event in events] == ["ONB-3", "ONB-3", "ONB-3"]
    assert events[0]["workflow_id"] is None
    assert events[1]["workflow_id"] == "WF-3"
