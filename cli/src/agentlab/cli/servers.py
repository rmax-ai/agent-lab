"""In-process lab servers for the CLI (SPEC §26).

``agent-lab dev`` and ``agent-lab scenario run`` boot the real backend app and
the real MockWorld app with uvicorn, in the same asyncio loop as the CLI, on
loopback ports. No containers, no separate processes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
import uvicorn
from fastapi import FastAPI

from agentlab.backend.app import create_app as create_backend_app
from agentlab.world.app import create_app as create_world_app

HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8080
DEFAULT_WORLD_PORT = 8000  # matches the device tools' MOCKWORLD_URL default

_READY_TIMEOUT_SECONDS = 15.0
_READY_POLL_SECONDS = 0.05
_STOP_TIMEOUT_SECONDS = 5.0


@dataclass
class LabServers:
    """A running backend + MockWorld pair, owned by one asyncio loop."""

    backend_port: int
    world_port: int
    _servers: list[uvicorn.Server] = field(default_factory=list)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    @property
    def backend_url(self) -> str:
        return f"http://{HOST}:{self.backend_port}"

    @property
    def world_url(self) -> str:
        return f"http://{HOST}:{self.world_port}"

    async def stop(self) -> None:
        """Ask every server to exit and wait for them to finish."""
        for server in self._servers:
            server.should_exit = True
        for task in self._tasks:
            try:
                await asyncio.wait_for(task, timeout=_STOP_TIMEOUT_SECONDS)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
            except Exception:
                pass


async def _wait_ready(server: uvicorn.Server, task: asyncio.Task[None], url: str) -> None:
    """Poll the app's OpenAPI route until the server accepts requests."""
    elapsed = 0.0
    async with httpx.AsyncClient() as client:
        while elapsed < _READY_TIMEOUT_SECONDS:
            if task.done():
                raise RuntimeError(f"server for {url} exited during startup: {task.exception()}")
            if server.started:
                try:
                    response = await client.get(f"{url}/openapi.json", timeout=1.0)
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
            await asyncio.sleep(_READY_POLL_SECONDS)
            elapsed += _READY_POLL_SECONDS
    raise RuntimeError(f"server for {url} did not become ready in {_READY_TIMEOUT_SECONDS}s")


async def _start_one(app: FastAPI, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(app, host=HOST, port=port, log_level="error")
    server = uvicorn.Server(config)
    task: asyncio.Task[None] = asyncio.create_task(server.serve())
    await _wait_ready(server, task, f"http://{HOST}:{port}")
    return server, task


async def start_lab(
    backend_port: int = DEFAULT_BACKEND_PORT,
    world_port: int = DEFAULT_WORLD_PORT,
) -> LabServers:
    """Boot the backend and MockWorld in-process and wait for readiness."""
    lab = LabServers(backend_port=backend_port, world_port=world_port)
    try:
        backend = await _start_one(create_backend_app(), backend_port)
        world = await _start_one(create_world_app(), world_port)
    except Exception:
        await lab.stop()
        raise
    lab._servers.extend([backend[0], world[0]])
    lab._tasks.extend([backend[1], world[1]])
    return lab
