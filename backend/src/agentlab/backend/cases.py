"""Case store service (SPEC §11, §19).

Owns ``onboarding_cases`` and the read aggregation that surfaces each case's
per-domain workflow status and summary counts. Every mutation writes a
``CASE_CREATED`` event through the shared event store helper.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from agentlab.backend.errors import BackendError
from agentlab.backend.events import emit_event
from agentlab.backend.models import (
    HumanTaskRow,
    OnboardingCase,
    WorkflowRun,
    json_loads,
)
from agentlab.sdk.events import EventType

_GOAL_PREFIX = "employee_"
_GOAL_SUFFIX = "_ready"


def _goal_to_domain(goal: str) -> str:
    """Map a goal string like ``employee_device_ready`` to ``device``."""
    if goal.startswith(_GOAL_PREFIX) and goal.endswith(_GOAL_SUFFIX):
        return goal[len(_GOAL_PREFIX) : -len(_GOAL_SUFFIX)]
    return goal


def _case_detail(session: Session, case: OnboardingCase) -> dict[str, Any]:
    workflows = session.exec(
        select(WorkflowRun).where(WorkflowRun.case_id == case.case_id)
    ).all()
    domain_status: dict[str, str] = {}
    for workflow in workflows:
        domain_status[_goal_to_domain(workflow.goal)] = workflow.status
    return {
        "case_id": case.case_id,
        "employee_id": case.employee_id,
        "status": case.status,
        "created_at": case.created_at.isoformat(),
        "context": json_loads(case.context_json, {}),
        "domain_status": domain_status,
    }


def create_case(
    session: Session,
    case_id: str,
    employee_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Create a case, emit ``CASE_CREATED``, and return its detail."""
    if session.get(OnboardingCase, case_id) is not None:
        raise BackendError(409, "CASE_EXISTS", f"Case {case_id!r} already exists")
    case = OnboardingCase(
        case_id=case_id,
        employee_id=employee_id,
        context_json=json.dumps(context),
    )
    session.add(case)
    emit_event(
        session,
        case_id,
        None,
        "coordinator",
        EventType.CASE_CREATED,
        {"case_id": case_id, "employee_id": employee_id},
    )
    session.commit()
    return _case_detail(session, case)


def get_case(session: Session, case_id: str) -> dict[str, Any]:
    """Return a case with per-domain workflow status aggregation."""
    case = session.get(OnboardingCase, case_id)
    if case is None:
        raise BackendError(404, "NOT_FOUND", f"Case {case_id!r} not found")
    return _case_detail(session, case)


def list_cases(session: Session) -> list[dict[str, Any]]:
    """List case summaries with blocker and open-approval counts."""
    cases = session.exec(
        select(OnboardingCase).order_by("created_at")
    ).all()
    summaries: list[dict[str, Any]] = []
    for case in cases:
        workflows = session.exec(
            select(WorkflowRun).where(WorkflowRun.case_id == case.case_id)
        ).all()
        blockers = sum(len(json_loads(wf.blockers_json, [])) for wf in workflows)
        open_approvals = len(
            session.exec(
                select(HumanTaskRow).where(
                    HumanTaskRow.case_id == case.case_id,
                    HumanTaskRow.status == "open",
                )
            ).all()
        )
        summaries.append(
            {
                "case_id": case.case_id,
                "employee_id": case.employee_id,
                "status": case.status,
                "blockers": blockers,
                "open_approvals": open_approvals,
            }
        )
    return summaries
