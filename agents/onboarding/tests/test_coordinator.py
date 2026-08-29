"""Deterministic vertical-slice tests for the Onboarding coordinator (SPEC §11).

The backend runs in-process over ``httpx.ASGITransport``; the coordinator is the
real :class:`CoordinatorAgent` (no real LLM, a canned ``before_model_callback``
answers any model turn); the domain agents are scripted stubs that call the
workflow routes exactly as a domain agent would. No ``agentlab.world`` code is
exercised by the coordinator.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from google.adk.models.llm_response import LlmResponse
from google.genai import types

import agentlab.onboarding.coordinator as coordinator_module
from agentlab.backend import db
from agentlab.backend.app import create_app
from agentlab.onboarding import CoordinatorAgent

ONBOARDING = "onboarding-agent"
DEVICE = "device-agent"
ACCESS = "access-agent"
OPS_LEAD = "ops-lead"


def _canned_response(callback_context: Any, llm_request: Any) -> LlmResponse:
    """Return a fixed model turn so no real model is ever reached."""
    del callback_context, llm_request
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text="ONBOARDING_READY")])
    )


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "backend.db"))
    monkeypatch.delenv("ALLOW_ANY_RESOLVER", raising=False)
    db.reset_engine()
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def coordinator(app: FastAPI) -> CoordinatorAgent:
    return CoordinatorAgent(
        backend_url="http://test",
        transport=httpx.ASGITransport(app=app),
    )


async def _drive(
    coordinator_run: Awaitable[dict[str, Any]],
    *stubs: Callable[[], Coroutine[Any, Any, None]],
) -> dict[str, Any]:
    """Run the coordinator while the scripted stubs react in the background."""
    tasks = [asyncio.create_task(stub()) for stub in stubs]
    try:
        return await coordinator_run
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _happy_stub(client: httpx.AsyncClient, case_id: str, agent_id: str) -> None:
    """Ack, run, and complete(verified) every workflow delegated to ``agent_id``."""
    handled: set[str] = set()
    while True:
        events = (await client.get(f"/cases/{case_id}/events")).json()["events"]
        for event in events:
            if (
                event["type"] == "WORKFLOW_DELEGATED"
                and event["payload"].get("target_agent_id") == agent_id
            ):
                workflow_id = event["workflow_id"]
                if workflow_id in handled:
                    continue
                handled.add(workflow_id)
                await client.post(
                    f"/workflows/{workflow_id}/ack", headers={"X-Agent-Id": agent_id}
                )
                await client.post(
                    f"/workflows/{workflow_id}/status",
                    headers={"X-Agent-Id": agent_id},
                    json={"workflow_id": workflow_id, "status": "running", "blockers": []},
                )
                await client.post(
                    f"/workflows/{workflow_id}/complete",
                    headers={"X-Agent-Id": agent_id},
                    json={"verified": True},
                )
        await asyncio.sleep(0.01)


async def _blocked_then_complete_stub(
    client: httpx.AsyncClient,
    case_id: str,
    agent_id: str,
    blocker: dict[str, str],
) -> None:
    """Block the first delegation, then complete the re-delegation."""
    handled: list[str] = []
    while True:
        events = (await client.get(f"/cases/{case_id}/events")).json()["events"]
        for event in events:
            if (
                event["type"] == "WORKFLOW_DELEGATED"
                and event["payload"].get("target_agent_id") == agent_id
            ):
                workflow_id = event["workflow_id"]
                if workflow_id in handled:
                    continue
                first = len(handled) == 0
                handled.append(workflow_id)
                await client.post(
                    f"/workflows/{workflow_id}/ack", headers={"X-Agent-Id": agent_id}
                )
                if first:
                    await client.post(
                        f"/workflows/{workflow_id}/status",
                        headers={"X-Agent-Id": agent_id},
                        json={
                            "workflow_id": workflow_id,
                            "status": "blocked",
                            "blockers": [blocker],
                        },
                    )
                else:
                    await client.post(
                        f"/workflows/{workflow_id}/status",
                        headers={"X-Agent-Id": agent_id},
                        json={
                            "workflow_id": workflow_id,
                            "status": "running",
                            "blockers": [],
                        },
                    )
                    await client.post(
                        f"/workflows/{workflow_id}/complete",
                        headers={"X-Agent-Id": agent_id},
                        json={"verified": True},
                    )
        await asyncio.sleep(0.01)


async def _ops_lead(client: httpx.AsyncClient, case_id: str) -> None:
    """Approve every open human task for the case as the ops lead."""
    while True:
        tasks = (await client.get("/tasks", params={"case_id": case_id})).json()
        for task in tasks:
            if task["status"] == "open":
                await client.post(
                    f"/tasks/{task['human_task_id']}/decision",
                    json={
                        "decision": {"decision": "approve", "note": "ok"},
                        "resolved_by": OPS_LEAD,
                    },
                )
        await asyncio.sleep(0.01)


async def test_run_onboarding_happy_path_verdict_ready(
    client: httpx.AsyncClient,
    coordinator: CoordinatorAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "POLL_INTERVAL_SECONDS", 0.02)
    await client.post(
        "/cases", json={"case_id": "ONB-HAPPY", "employee_id": "E42", "context": {}}
    )
    coordinator.agent.before_model_callback = _canned_response

    verdict = await _drive(
        coordinator.run_onboarding(
            "E42", case_id="ONB-HAPPY", workflows=["device", "access"]
        ),
        lambda: _happy_stub(client, "ONB-HAPPY", DEVICE),
        lambda: _happy_stub(client, "ONB-HAPPY", ACCESS),
    )

    assert verdict["verdict"] == "READY"
    assert set(verdict["ready_goals"]) == {"employee_device_ready", "employee_access_ready"}
    assert verdict["missing_goals"] == []

    # Delegation only: every delegated workflow targets a domain agent.
    events = (await client.get("/cases/ONB-HAPPY/events")).json()["events"]
    delegated = [e for e in events if e["type"] == "WORKFLOW_DELEGATED"]
    assert {e["payload"]["target_agent_id"] for e in delegated} == {DEVICE, ACCESS}
    assert all(e["actor"] == ONBOARDING for e in delegated)


async def test_blocked_workflow_hitl_then_redelegate_ready(
    client: httpx.AsyncClient,
    coordinator: CoordinatorAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "POLL_INTERVAL_SECONDS", 0.02)
    await client.post(
        "/cases", json={"case_id": "ONB-HITL", "employee_id": "E42", "context": {}}
    )
    coordinator.agent.before_model_callback = _canned_response
    blocker = {"code": "NO_INVENTORY", "description": "Standard device unavailable"}

    verdict = await _drive(
        coordinator.run_onboarding(
            "E42", case_id="ONB-HITL", workflows=["device", "access"]
        ),
        lambda: _happy_stub(client, "ONB-HITL", DEVICE),
        lambda: _blocked_then_complete_stub(client, "ONB-HITL", ACCESS, blocker),
        lambda: _ops_lead(client, "ONB-HITL"),
    )

    assert verdict["verdict"] == "READY"
    assert set(verdict["ready_goals"]) == {"employee_device_ready", "employee_access_ready"}

    tasks = (await client.get("/tasks", params={"case_id": "ONB-HITL"})).json()
    assert len(tasks) == 1
    assert tasks[0]["requested_by"] == ONBOARDING
    assert tasks[0]["requested_from"] == OPS_LEAD
    assert tasks[0]["resolved_by"] == OPS_LEAD

    events = (await client.get("/cases/ONB-HITL/events")).json()["events"]
    access_delegations = [
        e
        for e in events
        if e["type"] == "WORKFLOW_DELEGATED" and e["payload"]["target_agent_id"] == ACCESS
    ]
    assert len(access_delegations) == 2


async def test_stuck_workflow_verdict_not_ready(
    client: httpx.AsyncClient,
    coordinator: CoordinatorAgent,
) -> None:
    await client.post(
        "/cases", json={"case_id": "ONB-STUCK", "employee_id": "E42", "context": {}}
    )

    device = await coordinator.delegate_workflow(
        "ONB-STUCK", "employee_device_ready", DEVICE, "E42", {}
    )
    access = await coordinator.delegate_workflow(
        "ONB-STUCK", "employee_access_ready", ACCESS, "E42", {}
    )
    assert "error" not in device
    assert "error" not in access

    device_wf = device["workflow_id"]
    access_wf = access["workflow_id"]

    async def finish(workflow_id: str, agent_id: str) -> None:
        await client.post(f"/workflows/{workflow_id}/ack", headers={"X-Agent-Id": agent_id})
        await client.post(
            f"/workflows/{workflow_id}/status",
            headers={"X-Agent-Id": agent_id},
            json={"workflow_id": workflow_id, "status": "running", "blockers": []},
        )
        await client.post(
            f"/workflows/{workflow_id}/complete",
            headers={"X-Agent-Id": agent_id},
            json={"verified": True},
        )

    async def stuck(workflow_id: str, agent_id: str) -> None:
        await client.post(f"/workflows/{workflow_id}/ack", headers={"X-Agent-Id": agent_id})
        await client.post(
            f"/workflows/{workflow_id}/status",
            headers={"X-Agent-Id": agent_id},
            json={"workflow_id": workflow_id, "status": "running", "blockers": []},
        )

    await finish(device_wf, DEVICE)
    await stuck(access_wf, ACCESS)

    verdict = await coordinator.run_verdict("ONB-STUCK")

    assert verdict["verdict"] == "NOT_READY"
    assert "employee_device_ready" in verdict["ready_goals"]
    missing = {m["goal"]: m for m in verdict["missing_goals"]}
    assert "employee_access_ready" in missing
    assert missing["employee_access_ready"]["status"] == "running"


def test_coordinator_exposes_exactly_seven_tools(coordinator: CoordinatorAgent) -> None:
    assert len(coordinator.tools) == 7
    assert {tool.__name__ for tool in coordinator.tools} == {
        "create_case",
        "delegate_workflow",
        "get_case_status",
        "list_case_events",
        "request_human_intervention",
        "get_task_status",
        "escalate",
    }


def test_coordinator_never_imports_mockworld() -> None:
    source = Path(coordinator_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.append(module)

    assert all(name != "agentlab.world" and not name.startswith("agentlab.world.") for name in imported)
