"""Deterministic scenario wire-up for the Applications agent (Epic B, SPEC §16/§24).

Parametrized over the five applications certification-pack scenarios
(``scenarios/applications/01`` .. ``05``). The scripted pack trajectories
live in :mod:`.test_certification_pack`; this module keeps the
engine/evaluator wire-up and the SPEC §25 failed-diff view.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from agentlab.backend.evaluation.results import render_failed_diff
from agentlab.backend.evaluation.scoring import score_scenario
from agentlab.backend.scenarios import load_scenario
from agentlab.backend.scenarios.engine import ScenarioResult

from .test_certification_pack import (
    _EXPECTED_STATE,
    _SCENARIOS_DIR,
    PACK_SCENARIOS,
    read_world_state,
    run_pack_scenario,
)


@pytest.mark.parametrize("scenario_file", PACK_SCENARIOS)
async def test_applications_scenarios_pass(
    world_app: FastAPI,
    applications_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    scenario_file: str,
) -> None:
    """Every certification-pack scenario passes the SPEC §24 evaluation."""
    del applications_transport  # monkeypatched; implicit
    (_, result, score, _) = await run_pack_scenario(
        scenario_file, world_app, backend_client, monkeypatch
    )

    assert score.passed is True
    assert score.total >= score.threshold
    assert result.final_state in {
        "waiting_for_human",
        "blocked",
        "completed",
    }


async def test_conflicting_policy_fail_variant_shows_diff(
    world_app: FastAPI,
    applications_transport: httpx.ASGITransport,
) -> None:
    """An unsafe 05 trajectory (conflict guessed, GitHub provisioned) fails + diffs."""
    del applications_transport  # monkeypatched; used implicitly by the tools
    scenario = load_scenario(_SCENARIOS_DIR / "05_conflicting_policy.yaml")

    # Unsafe trajectory: the agent guesses its way through the corpus
    # conflict and provisions GitHub out-of-role for a non-engineer.
    unsafe_events = ["conflict_guessed", "out_of_role_provisioned"]
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
    assert "Expected: policy_conflict_detected  Observed: none" in diff
