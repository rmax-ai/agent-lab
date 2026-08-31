"""Shared fixtures for the Systems agent tests (temp world DB + ASGI clients).

Also makes the ``agents.systems`` package importable when collecting these
tests: the Systems agent lives in a plain code directory (no
``pyproject.toml``), so pytest needs the repository root on ``sys.path`` for
the ``agents`` namespace package to resolve. This mirrors how the Agent Lab
CLI will run team agents.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentlab.backend import db as backend_db  # noqa: E402
from agentlab.backend.app import create_app as create_backend_app  # noqa: E402
from agentlab.world import db as world_db  # noqa: E402
from agentlab.world.app import create_app as create_world_app  # noqa: E402

from ..tools import systems  # noqa: E402

_TOKEN = "test-token"


@pytest.fixture
def world_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build MockWorld + the backend over one temp shared SQLite file."""
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "lab.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENTLAB_SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("ALLOWED_DOMAINS", "systems-agent:systems")
    monkeypatch.delenv("ALLOW_ANY_RESOLVER", raising=False)  # enforce DEC-10
    # The systems tools read MOCKWORLD_URL / AGENTLAB_BACKEND_URL at import
    # time; the host is ignored by the ASGI transports, but keep them stable
    # so routing stays local.
    monkeypatch.setenv("MOCKWORLD_URL", "http://mockworld")
    monkeypatch.setenv("AGENTLAB_BACKEND_URL", "http://backend")
    world_db.reset_engine()
    backend_db.reset_engine()
    return create_world_app()


@pytest.fixture
def backend_app(world_app: FastAPI) -> FastAPI:
    """Build the backend app against the same temp database."""
    del world_app  # the env + engine reset happen in the world_app fixture
    return create_backend_app()


@pytest.fixture
def systems_transport(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> httpx.ASGITransport:
    """Route the systems tools' world HTTP calls at in-process MockWorld."""
    transport = httpx.ASGITransport(app=world_app)
    monkeypatch.setattr(systems, "TRANSPORT", transport)
    return transport


@pytest.fixture
def systems_backend_transport(
    backend_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> httpx.ASGITransport:
    """Route the provisioning tool's backend task-flow calls in-process."""
    transport = httpx.ASGITransport(app=backend_app)
    monkeypatch.setattr(systems, "BACKEND_TRANSPORT", transport)
    return transport


@pytest.fixture
async def backend_client(backend_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An async client for the in-process backend app."""
    transport = httpx.ASGITransport(app=backend_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://backend"
    ) as client:
        yield client
