"""``agent-lab status`` — lab reachability and the agent registry."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import httpx
import typer

DEFAULT_BACKEND_URL = "http://127.0.0.1:8080"
DEFAULT_WORLD_URL = "http://127.0.0.1:8000"
_PROBE_TIMEOUT_SECONDS = 2.0


async def _probe(client: httpx.AsyncClient, url: str, path: str) -> httpx.Response | None:
    try:
        response = await client.get(f"{url}{path}", timeout=_PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None
    return response if response.status_code < 500 else None


def _render_agent(agent: dict[str, Any]) -> str:
    agent_id = agent.get("agent_id", "?")
    state = str(agent.get("status", "unknown")).upper()
    tools = agent.get("tools", 0)
    knowledge_docs = agent.get("knowledge_docs", 0)
    return f"  {agent_id} {state} (tools={tools}, knowledge_docs={knowledge_docs})"


async def _status_async(backend_url: str, world_url: str) -> int:
    ok = True
    async with httpx.AsyncClient() as client:
        backend = await _probe(client, backend_url, "/agents")
        if backend is not None:
            typer.echo(f"backend:   reachable ({backend_url})")
        else:
            typer.echo(f"backend:   unreachable ({backend_url})")
            ok = False

        world = await _probe(client, world_url, "/openapi.json")
        if world is not None:
            typer.echo(f"mockworld: reachable ({world_url})")
        else:
            typer.echo(f"mockworld: unreachable ({world_url})")
            ok = False

        if backend is not None:
            agents = backend.json().get("agents", [])
            typer.echo("agents:")
            if agents:
                for agent in agents:
                    typer.echo(_render_agent(agent))
            else:
                typer.echo("  (none registered)")
    return 0 if ok else 1


def status_command(
    backend_url: Annotated[
        str, typer.Option("--backend-url", help="Backend base URL.")
    ] = DEFAULT_BACKEND_URL,
    world_url: Annotated[
        str, typer.Option("--world-url", help="MockWorld base URL.")
    ] = DEFAULT_WORLD_URL,
) -> None:
    """Print backend/MockWorld reachability and the registered agents."""
    exit_code = asyncio.run(_status_async(backend_url, world_url))
    if exit_code:
        raise typer.Exit(exit_code)
