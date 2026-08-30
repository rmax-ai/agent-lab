"""Evaluation engine package (SPEC §24/§25, DEC-13)."""

from agentlab.backend.evaluation.scoring import (
    CATEGORY_WEIGHTS,
    PASS_THRESHOLD,
    CategoryScore,
    EvaluationEngine,
    ScenarioScore,
    score_scenario,
)

__all__ = [
    "CATEGORY_WEIGHTS",
    "PASS_THRESHOLD",
    "CategoryScore",
    "EvaluationEngine",
    "ScenarioScore",
    "score_scenario",
]
