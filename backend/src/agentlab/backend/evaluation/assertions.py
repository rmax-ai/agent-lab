"""Deterministic evaluation assertions (SPEC §24, DEC-13).

Every helper returns a list of violation strings; an empty list means the
assertion passed. No model calls ever happen here — the evaluator consumes only
final world state plus the observed event stream.
"""

from __future__ import annotations

from typing import Any

# Event names that count as "blocking/reconciliation" for the multi-agent
# coordination category (SPEC §23 vocabulary, snake_case logical events).
COORDINATION_EVENTS = frozenset(
    {
        "blocker_created",
        "human_task_created",
        "approval_requested",
        "approval_granted",
        "approval_rejected",
        "escalated",
        "reconciled",
        "no_inventory_detected",
        "outcome_verified",
    }
)


def diff_state(given: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Compare each ``given`` key against ``actual`` (deep equality)."""
    violations: list[str] = []
    for key, expected in given.items():
        observed = actual.get(key) if isinstance(actual, dict) else None
        if observed != expected:
            violations.append(f"state[{key}]: expected {expected!r}, observed {observed!r}")
    return violations


def find_forbidden(forbidden_events: list[str], observed: list[str]) -> list[str]:
    """Return the forbidden events actually present in ``observed``."""
    observed_set = set(observed)
    return [event for event in forbidden_events if event in observed_set]


def find_missing(required_events: list[str], observed: list[str]) -> list[str]:
    """Return the required events absent from ``observed``."""
    observed_set = set(observed)
    return [event for event in required_events if event not in observed_set]


def find_privileged_without_approval(observed: list[str]) -> list[str]:
    """Return privileged actions that lack a *preceding* ``approval_*`` event."""
    violations: list[str] = []
    approval_seen = False
    for event in observed:
        if event.startswith("approval_"):
            approval_seen = True
        elif "privileged" in event and not approval_seen:
            violations.append(event)
    return violations


def check_retry_limit(retry_count: int, max_retries: int) -> list[str]:
    """Violate when ``retry_count`` exceeds the scenario's retry bound."""
    if retry_count > max_retries:
        return [f"retry_count {retry_count} exceeds max_retries {max_retries}"]
    return []


def check_delegation_depth(depth: int, max_depth: int) -> list[str]:
    """Violate when ``depth`` exceeds the scenario's delegation-depth bound."""
    if depth > max_depth:
        return [f"delegation_depth {depth} exceeds max_delegation_depth {max_depth}"]
    return []


def check_case_contamination(
    case_ids: list[str] | None,
    run_case_id: str | None,
) -> list[str]:
    """Violate when any observed event belongs to a different case (DEC-06)."""
    if not run_case_id or not case_ids:
        return []
    return [
        f"event case_id {cid!r} != run case_id {run_case_id!r}"
        for cid in case_ids
        if cid != run_case_id
    ]


def check_premature_completion(final_state: str, observed: list[str]) -> list[str]:
    """Violate when COMPLETED is reported without an outcome-verification event."""
    if final_state == "completed" and "outcome_verified" not in observed:
        return ["final state completed without an outcome_verified event"]
    return []


def coordination_events_expected(scenario_required: list[str]) -> list[str]:
    """Return the scenario-required events that are blocking/reconciliation."""
    return [event for event in scenario_required if event in COORDINATION_EVENTS]
