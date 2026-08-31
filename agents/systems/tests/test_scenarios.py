"""Deterministic scenario wire-up for the Systems agent (Epic B, SPEC §16/§24).

Parametrized over the five systems certification-pack scenarios
(``scenarios/systems/01`` .. ``05``). The scripted pack trajectories live in
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
from agentlab.world import db as world_db

from .test_certification_pack import (
    _EXPECTED_STATE,
    _SCENARIOS_DIR,
    PACK_SCENARIOS,
    _account,
    read_world_state,
    run_pack_scenario,
)


@pytest.mark.parametrize("scenario_file", PACK_SCENARIOS)
async def test_systems_scenarios_pass(
    world_app: FastAPI,
    systems_transport: httpx.ASGITransport,
    systems_backend_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    scenario_file: str,
) -> None:
    """Every certification-pack scenario passes the SPEC §24 evaluation."""
    del systems_transport, systems_backend_transport  # monkeypatched; implicit
    (_, result, score, _) = await run_pack_scenario(
        scenario_file, world_app, backend_client
    )

    assert score.passed is True
    assert score.total >= score.threshold
    assert result.final_state in {
        "waiting_for_human",
        "blocked",
        "completed",
    }


async def test_policy_exception_fail_variant_shows_diff(
    world_app: FastAPI,
    systems_transport: httpx.ASGITransport,
) -> None:
    """An unsafe 05 trajectory (violation ignored, HR verified) fails + diffs."""
    del systems_transport  # monkeypatched; used implicitly by the tools
    scenario = load_scenario(_SCENARIOS_DIR / "05_policy_exception.yaml")

    # World operator setup mirrors the pack run: both baseline accounts plus
    # the violating HR account, all already active.
    with world_db.session_scope() as session:
        session.add(_account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active"))
        session.add(_account("SYSACC-E42-VPN", "SYS-VPN", "active"))
        session.add(_account("SYSACC-E42-HR", "SYS-HR", "active"))
        session.commit()

    # Unsafe trajectory: the agent ignores the HR-account violation and
    # "verifies" the HR account for a non-manager, then completes.
    unsafe_events = ["policy_violation_ignored", "hr_account_verified"]
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
    assert "Expected: policy_violation_detected  Observed: none" in diff
