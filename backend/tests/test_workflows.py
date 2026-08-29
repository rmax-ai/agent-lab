"""Workflow engine route tests (SPEC §12)."""

from __future__ import annotations

from typing import Any

import httpx

CASE_ID = "ONB-1"
WORKFLOW_ID = "WF-1"
DEVICE = "device-agent"
COORDINATOR = "onboarding-agent"


async def _create_case(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.post(
        "/cases",
        json={"case_id": CASE_ID, "employee_id": "E42", "context": {}},
    )
    assert resp.status_code == 201
    return resp.json()


async def _start(
    client: httpx.AsyncClient,
    target: str = DEVICE,
    goal: str = "employee_device_ready",
) -> dict[str, Any]:
    resp = await client.post(
        "/workflows",
        headers={"X-Agent-Id": COORDINATOR},
        json={
            "workflow_id": WORKFLOW_ID,
            "case_id": CASE_ID,
            "goal": goal,
            "employee_id": "E42",
            "context": {"start_date": "2026-09-07"},
            "target_agent_id": target,
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _ack(client: httpx.AsyncClient, agent: str = DEVICE) -> dict[str, Any]:
    resp = await client.post(
        f"/workflows/{WORKFLOW_ID}/ack",
        headers={"X-Agent-Id": agent},
    )
    assert resp.status_code == 200
    return resp.json()


async def test_full_happy_path(client: httpx.AsyncClient) -> None:
    await _create_case(client)

    started = await _start(client)
    assert started["status"] == "acknowledged"
    assert started["agent_id"] == DEVICE

    acked = await _ack(client)
    assert acked["status"] == "running"

    reported = await client.post(
        f"/workflows/{WORKFLOW_ID}/status",
        headers={"X-Agent-Id": DEVICE},
        json={"workflow_id": WORKFLOW_ID, "status": "running", "blockers": []},
    )
    assert reported.status_code == 200
    assert reported.json()["status"] == "running"

    completed = await client.post(
        f"/workflows/{WORKFLOW_ID}/complete",
        headers={"X-Agent-Id": DEVICE},
        json={"verified": True},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["verified"] is True

    events = (await client.get(f"/cases/{CASE_ID}/events")).json()["events"]
    assert [event["type"] for event in events] == [
        "CASE_CREATED",
        "WORKFLOW_DELEGATED",
        "WORKFLOW_ACKNOWLEDGED",
        "WORKFLOW_STATUS",
        "OUTCOME_VERIFIED",
    ]
    assert events[1]["actor"] == COORDINATOR
    assert events[2]["actor"] == DEVICE


async def test_ownership_mismatch_returns_403(client: httpx.AsyncClient) -> None:
    await _create_case(client)
    await _start(client)

    resp = await client.post(
        f"/workflows/{WORKFLOW_ID}/ack",
        headers={"X-Agent-Id": "access-agent"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_complete_requires_verification(client: httpx.AsyncClient) -> None:
    await _create_case(client)
    await _start(client)
    await _ack(client)

    rejected = await client.post(
        f"/workflows/{WORKFLOW_ID}/complete",
        headers={"X-Agent-Id": DEVICE},
        json={"verified": False},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "VERIFICATION_REQUIRED"

    accepted = await client.post(
        f"/workflows/{WORKFLOW_ID}/complete",
        headers={"X-Agent-Id": DEVICE},
        json={"verified": True},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "completed"


async def test_blocked_then_resume_then_complete(client: httpx.AsyncClient) -> None:
    await _create_case(client)
    await _start(client)
    await _ack(client)

    blocked = await client.post(
        f"/workflows/{WORKFLOW_ID}/status",
        headers={"X-Agent-Id": DEVICE},
        json={
            "workflow_id": WORKFLOW_ID,
            "status": "blocked",
            "blockers": [{"code": "NO_INVENTORY", "description": "Standard device unavailable"}],
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["blockers"][0]["code"] == "NO_INVENTORY"

    resumed = await client.post(
        f"/workflows/{WORKFLOW_ID}/status",
        headers={"X-Agent-Id": DEVICE},
        json={"workflow_id": WORKFLOW_ID, "status": "running", "blockers": []},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"

    completed = await client.post(
        f"/workflows/{WORKFLOW_ID}/complete",
        headers={"X-Agent-Id": DEVICE},
        json={"verified": True},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    events = (await client.get(f"/cases/{CASE_ID}/events")).json()["events"]
    assert "BLOCKER_CREATED" in [event["type"] for event in events]


async def test_waiting_for_human_then_decision_resumes(client: httpx.AsyncClient) -> None:
    await _create_case(client)
    await _start(client)
    await _ack(client)

    waiting = await client.post(
        f"/workflows/{WORKFLOW_ID}/status",
        headers={"X-Agent-Id": DEVICE},
        json={"workflow_id": WORKFLOW_ID, "status": "waiting_for_human", "blockers": []},
    )
    assert waiting.status_code == 200
    assert waiting.json()["status"] == "waiting_for_human"

    task = await client.post(
        "/tasks",
        json={
            "human_task_id": "HT-1",
            "case_id": CASE_ID,
            "workflow_id": WORKFLOW_ID,
            "requested_by": DEVICE,
            "requested_from": "sre-manager",
            "type": "APPROVAL",
            "context": {"reason": "MacBook Pro unavailable"},
            "allowed_actions": ["approve", "reject"],
            "status": "open",
            "created_at": "2026-09-07T09:00:00",
        },
    )
    assert task.status_code == 201

    decision = await client.post(
        "/tasks/HT-1/decision",
        json={"decision": {"decision": "approve"}, "resolved_by": "sre-manager"},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "resolved"

    detail = (await client.get(f"/cases/{CASE_ID}")).json()
    assert detail["domain_status"]["device"] == "running"


async def test_fail_retries_then_terminal(client: httpx.AsyncClient) -> None:
    await _create_case(client)
    await _start(client)
    await _ack(client)

    for expected in (1, 2, 3):
        resp = await client.post(
            f"/workflows/{WORKFLOW_ID}/fail",
            headers={"X-Agent-Id": DEVICE},
            json={"reason": "transient failure"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        assert resp.json()["retry_count"] == expected

    terminal = await client.post(
        f"/workflows/{WORKFLOW_ID}/fail",
        headers={"X-Agent-Id": DEVICE},
        json={"reason": "permanent failure"},
    )
    assert terminal.status_code == 200
    assert terminal.json()["status"] == "failed"
    assert terminal.json()["retry_count"] == 3
