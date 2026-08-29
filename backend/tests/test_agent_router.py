"""Agent router REST tests (SPEC §13/§26)."""

from __future__ import annotations

import httpx


async def test_agents_initially_empty(client: httpx.AsyncClient) -> None:
    resp = await client.get("/agents")
    assert resp.status_code == 200
    assert resp.json() == {"agents": []}


async def test_register_and_reflect(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/agents/register",
        json={"agent_id": "device-agent", "tools": 5, "knowledge_docs": 6},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["agent_id"] == "device-agent"
    assert body["status"] == "online"
    assert body["tools"] == 5
    assert body["knowledge_docs"] == 6
    assert "connected_at" in body

    listed = await client.get("/agents")
    assert listed.status_code == 200
    agents = listed.json()["agents"]
    assert len(agents) == 1
    assert agents[0]["agent_id"] == "device-agent"
    assert agents[0]["status"] == "online"
    assert agents[0]["tools"] == 5
    assert agents[0]["knowledge_docs"] == 6


async def test_duplicate_register_updates(client: httpx.AsyncClient) -> None:
    first = await client.post(
        "/agents/register",
        json={"agent_id": "device-agent", "tools": 1, "knowledge_docs": 2},
    )
    assert first.status_code == 201
    assert first.json()["tools"] == 1

    second = await client.post(
        "/agents/register",
        json={"agent_id": "device-agent", "tools": 7, "knowledge_docs": 9},
    )
    assert second.status_code == 201
    assert second.json()["tools"] == 7

    listed = await client.get("/agents")
    agents = listed.json()["agents"]
    assert len(agents) == 1
    assert agents[0]["agent_id"] == "device-agent"
    assert agents[0]["tools"] == 7
    assert agents[0]["knowledge_docs"] == 9
