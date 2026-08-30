"""``agent-lab dev`` — boot the lab and bring the cwd's team agent online.

SPEC §26 startup output, printed only after every check really passed::

    ✓ connected to Agent Lab
    ✓ MockWorld available
    ✓ knowledge loaded: N documents
    ✓ tools registered: N
    ✓ <agent-id> ONLINE
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from agentlab.cli.servers import DEFAULT_BACKEND_PORT, DEFAULT_WORLD_PORT, start_lab
from agentlab.sdk.transport import AgentLabTransport

_WELCOME_TIMEOUT_SECONDS = 10.0
_WELCOME_POLL_SECONDS = 0.05


def _load_team_agent(project_dir: Path) -> Any:
    """Import ``build_team_agent`` from ``<project_dir>/agent.py`` and call it."""
    agent_path = project_dir / "agent.py"
    if not agent_path.is_file():
        typer.echo(
            f"error: no agent.py in {project_dir} — run `agent-lab init <name>` first",
            err=True,
        )
        raise typer.Exit(2)
    spec = importlib.util.spec_from_file_location("agentlab_team_agent", agent_path)
    if spec is None or spec.loader is None:
        typer.echo(f"error: cannot import {agent_path}", err=True)
        raise typer.Exit(2)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        typer.echo(f"error: {agent_path} failed to import: {exc}", err=True)
        raise typer.Exit(2) from exc
    builder = getattr(module, "build_team_agent", None)
    if not callable(builder):
        typer.echo(f"error: {agent_path} does not define build_team_agent()", err=True)
        raise typer.Exit(2)
    return builder()


def _count_knowledge_docs(project_dir: Path, agent: Any) -> int:
    """Count the Markdown documents the agent's knowledge provider can see."""
    provider = getattr(agent, "knowledge", None)
    documents = getattr(provider, "documents", None)
    if isinstance(documents, list):
        return len(documents)
    return len(list((project_dir / "knowledge").glob("*.md")))


def _count_tools(agent: Any) -> int:
    tools = getattr(getattr(agent, "agent", None), "tools", None)
    return len(tools) if isinstance(tools, list) else 0


async def _register_agent(
    backend_url: str, agent_id: str, tools: int, knowledge_docs: int
) -> None:
    async with httpx.AsyncClient(base_url=backend_url) as client:
        response = await client.post(
            "/agents/register",
            json={"agent_id": agent_id, "tools": tools, "knowledge_docs": knowledge_docs},
        )
        response.raise_for_status()


async def _connect_transport(
    backend_url: str, agent_id: str, tools: int, knowledge_docs: int
) -> AgentLabTransport:
    """Connect to the WS hub and complete the hello → welcome handshake."""
    transport = AgentLabTransport(f"{backend_url}/ws/agents", agent_id)
    await transport.connect()
    await transport._send_frame(  # the hub waits for `hello` before `welcome`
        {
            "type": "hello",
            "agent_id": agent_id,
            "tools": tools,
            "knowledge_docs": knowledge_docs,
        }
    )
    elapsed = 0.0
    while transport.last_welcome is None and elapsed < _WELCOME_TIMEOUT_SECONDS:
        await asyncio.sleep(_WELCOME_POLL_SECONDS)
        elapsed += _WELCOME_POLL_SECONDS
    if transport.last_welcome is None:
        await transport.disconnect()
        raise RuntimeError("no welcome frame from the Agent Lab hub")
    return transport


async def _probe_mockworld(world_url: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{world_url}/openapi.json", timeout=2.0)
            return response.status_code == 200
    except httpx.HTTPError:
        return False


async def _dev_async(project_dir: Path, backend_port: int, world_port: int, once: bool) -> None:
    lab = await start_lab(backend_port=backend_port, world_port=world_port)
    transport: AgentLabTransport | None = None
    try:
        # Team tools read MOCKWORLD_URL at import time; point them at the world
        # this command just booted before importing the team's agent.py.
        os.environ["MOCKWORLD_URL"] = lab.world_url
        agent = _load_team_agent(project_dir)
        knowledge_docs = _count_knowledge_docs(project_dir, agent)
        tools = _count_tools(agent)

        await _register_agent(lab.backend_url, agent.id, tools, knowledge_docs)
        transport = await _connect_transport(lab.backend_url, agent.id, tools, knowledge_docs)
        mockworld_ok = await _probe_mockworld(lab.world_url)

        typer.echo("✓ connected to Agent Lab")
        if mockworld_ok:
            typer.echo("✓ MockWorld available")
        else:
            typer.echo("✗ MockWorld unavailable")
        typer.echo(f"✓ knowledge loaded: {knowledge_docs} documents")
        typer.echo(f"✓ tools registered: {tools}")
        typer.echo(f"✓ {agent.id} ONLINE")

        if not once:
            typer.echo(
                "dev loop running — edit agent.py / instructions.md / knowledge, Ctrl-C to stop"
            )
            await asyncio.Event().wait()  # until cancelled by Ctrl-C
    finally:
        if transport is not None:
            await transport.disconnect()
        await lab.stop()


def dev_command(
    port: Annotated[int, typer.Option("--port", help="Backend port.")] = DEFAULT_BACKEND_PORT,
    world_port: Annotated[
        int, typer.Option("--world-port", help="MockWorld port.")
    ] = DEFAULT_WORLD_PORT,
    once: Annotated[
        bool, typer.Option("--once", help="Print the startup checks and exit.")
    ] = False,
) -> None:
    """Boot the lab in-process and bring the current directory's agent online."""
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_dev_async(Path.cwd(), port, world_port, once))
