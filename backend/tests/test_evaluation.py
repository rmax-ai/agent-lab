"""Deterministic evaluation/scoring tests (SPEC §24/§25, DEC-13)."""

from __future__ import annotations

from agentlab.backend.evaluation import CATEGORY_WEIGHTS, EvaluationEngine
from agentlab.backend.evaluation.results import render_failed_diff, render_summary_table
from agentlab.backend.scenarios.engine import ScenarioResult
from agentlab.backend.scenarios.models import Scenario, ScenarioExpected


def _timeline() -> list[dict]:
    return [
        {"ts": 0.0, "kind": "reset", "detail": {}},
        {"ts": 0.1, "kind": "load", "detail": {}},
        {"ts": 0.2, "kind": "agent_started", "detail": {}},
        {"ts": 0.3, "kind": "agent_ended", "detail": {}},
    ]


def test_category_weights_sum_to_100() -> None:
    assert sum(CATEGORY_WEIGHTS.values()) == 100


def test_fully_passing_run_scores_100_and_passes() -> None:
    scenario = Scenario(
        id="device-happy-path",
        expected=ScenarioExpected(
            required_events=["inventory_checked", "device_reserved"],
            allowed_final_states=["completed"],
            forbidden_events=["unavailable_device_reserved"],
        ),
    )
    result = ScenarioResult(
        scenario_id=scenario.id,
        timeline=_timeline(),
        final_state="completed",
        events=["inventory_checked", "device_reserved", "outcome_verified"],
    )

    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state={"macbook_pro_14": 0},
        expected_state={"macbook_pro_14": 0},
        retry_count=0,
        delegation_depth=0,
        case_ids=["ONB-1"] * 3,
        run_case_id="ONB-1",
    )

    assert score.total == 100
    assert score.passed is True


def test_failed_run_reports_diff_with_expected_observed_none() -> None:
    scenario = Scenario(
        id="device-inventory-exhausted",
        expected=ScenarioExpected(
            required_events=["inventory_checked", "no_inventory_detected"],
            allowed_final_states=["completed", "waiting_for_human"],
            forbidden_events=["unavailable_device_reserved"],
        ),
    )
    result = ScenarioResult(
        scenario_id=scenario.id,
        timeline=_timeline(),
        final_state="failed",
        events=["unavailable_device_reserved"],
    )

    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state={},
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


def test_summary_table_and_n_of_m_line() -> None:
    summary = render_summary_table(
        "device agent",
        [
            ("Happy path", True),
            ("No inventory", True),
            ("Delivery failure", False),
        ],
    )

    assert summary.splitlines()[0] == "DEVICE AGENT"
    assert "4 / 5" not in summary
    assert "2 / 3" in summary
    assert "Delivery failure" in summary
    assert "FAIL" in summary
