"""Human task service route tests (SPEC §15, DEC-10)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

CASE_ID = "ONB-1"
WORKFLOW_ID = "WF-1"
DEVICE = "device-agent"
COORDINATOR = "onboarding-agent"
MANAGER = "sre-manager"


def _task_payload(task_id: str = "HT-1", requested_from: str = MANAGER) -> dict[str, Any]:
    return {
        "human_task_id": task_id,
        "case_id": CASE_ID,
        "workflow_id": WORKFLOW_ID,
        "requested_by": DEVICE,
        "requested_from": requested_from,
        "type": "APPROVAL",
        "context": {"reason": "MacBook Pro unavailable"},
        "allowed_actions": ["approve", "reject"],
        "status": "open",
        "created_at": "2026-09-07T09:00:00",
    }


async def _setup_waiting(
    client: httpx.AsyncClient,
    task_id: str = "HT-1",
    requested_from: str = MANAGER,
) -> httpx.Response:
    await client.post(
        "/cases",
        json={"case_id": CASE_ID, "employee_id": "E42", "context": {}},
    )
    await client.post(
        "/workflows",
        headers={"X-Agent-Id": COORDINATOR},
        json={
            "workflow_id": WORKFLOW_ID,
            "case_id": CASE_ID,
            "goal": "employee_device_ready",
            "employee_id": "E42",
            "context": {},
            "target_agent_id": DEVICE,
        },
    )
    await client.post(f"/workflows/{WORKFLOW_ID}/ack", headers={"X-Agent-Id": DEVICE})
    await client.post(
        f"/workflows/{WORKFLOW_ID}/status",
        headers={"X-Agent-Id": DEVICE},
        json={"workflow_id": WORKFLOW_ID, "status": "waiting_for_human", "blockers": []},
    )
    return await client.post(
        "/tasks",
        json=_task_payload(task_id=task_id, requested_from=requested_from),
    )


async def test_create_and_authorized_decide(client: httpx.AsyncClient) -> None:
    created = await _setup_waiting(client)
    assert created.status_code == 201
    assert created.json()["status"] == "open"
    assert created.json()["requested_from"] == MANAGER

    decision = await client.post(
        "/tasks/HT-1/decision",
        json={"decision": {"decision": "approve", "note": "ok"}, "resolved_by": MANAGER},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "resolved"
    assert decision.json()["resolved_by"] == MANAGER
    assert decision.json()["decision"] == {"decision": "approve", "note": "ok"}

    detail = (await client.get(f"/cases/{CASE_ID}")).json()
    assert detail["domain_status"]["device"] == "running"

    events = (await client.get(f"/cases/{CASE_ID}/events")).json()["events"]
    event_types = [event["type"] for event in events]
    assert "HUMAN_TASK_CREATED" in event_types
    assert "APPROVAL_GRANTED" in event_types


async def test_unauthorized_resolver_returns_403(client: httpx.AsyncClient) -> None:
    created = await _setup_waiting(client)
    assert created.status_code == 201

    resp = await client.post(
        "/tasks/HT-1/decision",
        json={"decision": {"decision": "approve"}, "resolved_by": "someone-else"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "UNAUTHORIZED_APPROVER"

    task = await client.get("/tasks/HT-1")
    assert task.status_code == 200
    assert task.json()["status"] == "open"


async def test_allow_any_resolver_bypass(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup_waiting(client)
    monkeypatch.setenv("ALLOW_ANY_RESOLVER", "1")

    resp = await client.post(
        "/tasks/HT-1/decision",
        json={"decision": {"decision": "approve"}, "resolved_by": "anyone"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


async def test_non_open_task_decide_returns_409(client: httpx.AsyncClient) -> None:
    await _setup_waiting(client)

    first = await client.post(
        "/tasks/HT-1/decision",
        json={"decision": {"decision": "approve"}, "resolved_by": MANAGER},
    )
    assert first.status_code == 200

    second = await client.post(
        "/tasks/HT-1/decision",
        json={"decision": {"decision": "approve"}, "resolved_by": MANAGER},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TASK_NOT_OPEN"


async def test_reject_emits_approval_rejected_and_resumes(client: httpx.AsyncClient) -> None:
    await _setup_waiting(client)

    resp = await client.post(
        "/tasks/HT-1/decision",
        json={"decision": {"decision": "reject"}, "resolved_by": MANAGER},
    )
    assert resp.status_code == 200
    assert resp.json()["decision"] == {"decision": "reject"}

    events = (await client.get(f"/cases/{CASE_ID}/events")).json()["events"]
    event_types = [event["type"] for event in events]
    assert "APPROVAL_REJECTED" in event_types

    detail = (await client.get(f"/cases/{CASE_ID}")).json()
    assert detail["domain_status"]["device"] == "running"


async def test_list_tasks_filters_by_case(client: httpx.AsyncClient) -> None:
    await _setup_waiting(client, task_id="HT-1")
    await client.post("/tasks", json=_task_payload(task_id="HT-2"))

    all_tasks = await client.get("/tasks")
    assert all_tasks.status_code == 200
    assert len(all_tasks.json()) == 2

    filtered = await client.get(f"/tasks?case_id={CASE_ID}")
    assert filtered.status_code == 200
    assert len(filtered.json()) == 2
    assert {t["human_task_id"] for t in filtered.json()} == {"HT-1", "HT-2"}
