"""Deterministic scenario wire-up for the Device agent (A.11 + A.12 + A.13).

Parametrized over the five device certification-pack scenarios
(``scenarios/devices/01`` .. ``05``; ``03_no_inventory.yaml`` supersedes the old
device-inventory-exhausted scenario, and ``01_happy_path.yaml`` supersedes
device-happy-path). The scripted pack trajectories live in
:mod:`.test_certification_pack`; this module keeps the engine/evaluator
wire-up and the SPEC §25 failed-diff view.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agentlab.backend import db as backend_db
from agentlab.backend.app import create_app as create_backend_app
from agentlab.backend.evaluation.results import render_failed_diff
from agentlab.world import db as world_db
from agentlab.world.app import create_app as create_world_app

from ..tools import device
from .test_certification_pack import PACK_SCENARIOS, run_pack_scenario

_TOKEN = "test-token"


@pytest.fixture
def world_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build MockWorld + the backend over one temp shared SQLite file."""
    monkeypatch.setenv("AGENTLAB_DB", str(tmp_path / "lab.db"))
    monkeypatch.setenv("SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENTLAB_SIMULATION_TOKEN", _TOKEN)
    monkeypatch.setenv("ALLOWED_DOMAINS", "device-agent:devices")
    monkeypatch.delenv("ALLOW_ANY_RESOLVER", raising=False)  # enforce DEC-10
    monkeypatch.setenv("MOCKWORLD_URL", "http://mockworld")
    world_db.reset_engine()
    backend_db.reset_engine()
    return create_world_app()


@pytest.fixture
def backend_app(world_app: FastAPI) -> FastAPI:
    """Build the backend app against the same temp database."""
    del world_app  # the env + engine reset happen in the world_app fixture
    return create_backend_app()


@pytest.fixture
def device_transport(
    world_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> httpx.ASGITransport:
    """Route the device tools' HTTP calls at the in-process MockWorld app."""
    transport = httpx.ASGITransport(app=world_app)
    monkeypatch.setattr(device, "TRANSPORT", transport)
    return transport


@pytest.fixture
async def backend_client(backend_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An async client for the in-process backend app."""
    transport = httpx.ASGITransport(app=backend_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://backend"
    ) as client:
        yield client


@pytest.mark.parametrize("scenario_file", PACK_SCENARIOS)
async def test_device_scenarios_pass(
    world_app: FastAPI,
    device_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    scenario_file: str,
) -> None:
    """Every certification-pack scenario passes the SPEC §24 evaluation."""
    del device_transport  # monkeypatched; used implicitly by the tools
    (_, result, score, _) = await run_pack_scenario(
        scenario_file, world_app, backend_client
    )

    assert score.passed is True
    assert score.total >= score.threshold
    assert result.final_state in {
        "waiting_for_human",
        "completed",
    }


async def test_no_inventory_fail_variant_shows_diff(
    world_app: FastAPI,
    device_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """The unsafe 03 trajectory fails and renders the §25 diff view."""
    del device_transport
    (scenario, result, score, _) = await run_pack_scenario(
        "03_no_inventory.yaml", world_app, backend_client, mode="fail"
    )

    assert score.passed is False
    assert score.total < score.threshold

    diff = render_failed_diff(
        scenario.id,
        scenario.expected.required_events,
        result.events,
        result.final_state,
    )
    assert "Expected: no_inventory_detected  Observed: none" in diff
