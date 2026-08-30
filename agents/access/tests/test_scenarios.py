"""Deterministic scenario wire-up for the Access agent (Epic B, SPEC §16/§24).

Parametrized over the five access certification-pack scenarios
(``scenarios/access/01`` .. ``05``). The scripted pack trajectories live in
:mod:`.test_certification_pack`; this module keeps the engine/evaluator
wire-up and the SPEC §25 failed-diff view.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from agentlab.backend.evaluation.results import render_failed_diff
from agentlab.backend.evaluation.scoring import score_scenario
from agentlab.backend.scenarios import load_scenario
from agentlab.backend.scenarios.engine import ScenarioResult

from ..tools import access
from .test_certification_pack import (
    _EXPECTED_STATE,
    _SCENARIOS_DIR,
    PACK_SCENARIOS,
    read_world_state,
    run_pack_scenario,
)


@pytest.mark.parametrize("scenario_file", PACK_SCENARIOS)
async def test_access_scenarios_pass(
    world_app: FastAPI,
    access_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    scenario_file: str,
) -> None:
    """Every certification-pack scenario passes the SPEC §24 evaluation."""
    del access_transport  # monkeypatched; used implicitly by the tools
    (_, result, score, _) = await run_pack_scenario(
        scenario_file, world_app, backend_client
    )

    assert score.passed is True
    assert score.total >= score.threshold
    assert result.final_state in {
        "waiting_for_human",
        "completed",
    }


async def test_duplicate_request_fail_variant_shows_diff(
    world_app: FastAPI,
    access_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
) -> None:
    """An unsafe 05 trajectory (duplicate created) fails and renders the diff."""
    del backend_client
    scenario = load_scenario(_SCENARIOS_DIR / "05_duplicate_request.yaml")

    # Unsafe trajectory: request the already-held group anyway, really
    # double-creating it in the world, and complete without verifying.
    created = await access.request_group_access("E42", "GRP-STANDARD")
    assert created["requested"] is True
    unsafe_events = ["access_requested", "duplicate_request_granted"]
    result = ScenarioResult(
        scenario_id=scenario.id,
        final_state="completed",
        events=unsafe_events,
    )
    score = score_scenario(
        scenario,
        result,
        final_world_state=await read_world_state("E42"),
        expected_state=_EXPECTED_STATE[scenario.id],
    )

    assert score.passed is False
    assert score.total < score.threshold

    diff = render_failed_diff(
        scenario.id,
        scenario.expected.required_events,
        result.events,
        result.final_state,
    )
    assert "Expected: duplicate_request_detected  Observed: none" in diff
