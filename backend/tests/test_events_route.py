"""POST /events route tests (SPEC §23, issue #27).

A registered agent appends trace events; the route forces ``actor`` to the
``X-Agent-Id`` identity, sets ``ts`` server-side, validates referential
integrity, and writes only through the single ``emit_event`` helper.
"""

from __future__ import annotations

import httpx

AGENT = "device-agent"
OTHER = "access-agent"


async def _register(client: httpx.AsyncClient, agent_id: str = AGENT) -> None:
    response = await client.post("/agents/register", json={"agent_id": agent_id})
    assert response.status_code == 201


async def _create_case(client: httpx.AsyncClient, case_id: str = "ONB-42") -> None:
    response = await client.post(
        "/cases",
        json={"case_id": case_id, "employee_id": "E42", "context": {}},
    )
    assert response.status_code == 201


async def _create_workflow(
    client: httpx.AsyncClient,
    workflow_id: str = "WF-D-42",
    case_id: str = "ONB-42",
) -> None:
    response = await client.post(
        "/workflows",
        headers={"X-Agent-Id": "onboarding-agent"},
        json={
            "workflow_id": workflow_id,
            "case_id": case_id,
            "goal": "employee_device_ready",
            "employee_id": "E42",
            "context": {},
            "target_agent_id": AGENT,
        },
    )
    assert response.status_code == 201


async def test_registered_agent_emits_event(client: httpx.AsyncClient) -> None:
    await _register(client)
    await _create_case(client)

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": AGENT},
        json={
            "case_id": "ONB-42",
            "type": "TOOL_CALL",
            "payload": {"tool": "reserve_device", "args": {"sku": "macbook_pro_14"}},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["case_id"] == "ONB-42"
    assert body["workflow_id"] is None
    assert body["actor"] == AGENT
    assert body["type"] == "TOOL_CALL"
    assert body["payload"]["tool"] == "reserve_device"
    assert body["ts"]  # server-set timestamp

    listing = await client.get("/cases/ONB-42/events")
    assert listing.status_code == 200
    types = [event["type"] for event in listing.json()["events"]]
    assert "TOOL_CALL" in types  # alongside the CASE_CREATED the case wrote


async def test_actor_is_forced_to_header_identity(client: httpx.AsyncClient) -> None:
    await _register(client)
    await _create_case(client)

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": AGENT},
        json={
            "case_id": "ONB-42",
            "type": "TOOL_RESULT",
            "actor": OTHER,  # spoof attempt: ignored
            "ts": "2001-01-01T00:00:00+00:00",  # client clock: ignored
            "payload": {},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["actor"] == AGENT
    assert not body["ts"].startswith("2001-01-01")

    listing = await client.get("/cases/ONB-42/events")
    stored = [e for e in listing.json()["events"] if e["type"] == "TOOL_RESULT"]
    assert stored and all(e["actor"] == AGENT for e in stored)


async def test_unregistered_agent_is_rejected(client: httpx.AsyncClient) -> None:
    await _create_case(client)

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": "ghost-agent"},
        json={"case_id": "ONB-42", "type": "TOOL_CALL", "payload": {}},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_missing_agent_header_is_rejected(client: httpx.AsyncClient) -> None:
    await _create_case(client)

    response = await client.post(
        "/events",
        json={"case_id": "ONB-42", "type": "TOOL_CALL", "payload": {}},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_unknown_case_is_rejected(client: httpx.AsyncClient) -> None:
    await _register(client)

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": AGENT},
        json={"case_id": "ONB-NOPE", "type": "TOOL_CALL", "payload": {}},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_workflow_scoped_event_is_persisted(client: httpx.AsyncClient) -> None:
    await _register(client)
    await _create_case(client)
    await _create_workflow(client)

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": AGENT},
        json={
            "case_id": "ONB-42",
            "workflow_id": "WF-D-42",
            "type": "KNOWLEDGE_READ",
            "payload": {"doc": "devices/fulfillment.md"},
        },
    )

    assert response.status_code == 201
    assert response.json()["workflow_id"] == "WF-D-42"


async def test_unknown_workflow_is_rejected(client: httpx.AsyncClient) -> None:
    await _register(client)
    await _create_case(client)

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": AGENT},
        json={
            "case_id": "ONB-42",
            "workflow_id": "WF-NOPE",
            "type": "TOOL_CALL",
            "payload": {},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_workflow_from_another_case_is_rejected(client: httpx.AsyncClient) -> None:
    await _register(client)
    await _create_case(client)
    await _create_case(client, case_id="ONB-99")
    await _create_workflow(client, workflow_id="WF-D-99", case_id="ONB-99")

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": AGENT},
        json={
            "case_id": "ONB-42",
            "workflow_id": "WF-D-99",
            "type": "TOOL_CALL",
            "payload": {},
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WORKFLOW_CASE_MISMATCH"


async def test_unknown_event_type_is_rejected(client: httpx.AsyncClient) -> None:
    await _register(client)
    await _create_case(client)

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": AGENT},
        json={"case_id": "ONB-42", "type": "MADE_UP", "payload": {}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_non_dict_payload_is_rejected(client: httpx.AsyncClient) -> None:
    await _register(client)
    await _create_case(client)

    response = await client.post(
        "/events",
        headers={"X-Agent-Id": AGENT},
        json={"case_id": "ONB-42", "type": "TOOL_CALL", "payload": "not-a-dict"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
