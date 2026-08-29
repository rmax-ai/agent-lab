"""Human-in-the-loop task service (SPEC §15, DEC-10).

HITL is first-class persisted state: a task row is created, a human decision is
recorded, an approval event is emitted, and the linked workflow resumes from
``WAITING_FOR_HUMAN`` back to ``RUNNING`` (SPEC §15, PATTERNS §5).
"""

from __future__ import annotations

import os
from typing import Any

from sqlmodel import Session, select

from agentlab.backend.errors import BackendError
from agentlab.backend.events import emit_event
from agentlab.backend.models import (
    HumanTaskRow,
    WorkflowRun,
    json_dumps,
    json_loads,
    utcnow,
)
from agentlab.backend.statemachine import validate_transition
from agentlab.sdk.events import EventType
from agentlab.sdk.protocols import HumanTask, WorkflowState


def _task_dict(row: HumanTaskRow) -> dict[str, Any]:
    return {
        "human_task_id": row.human_task_id,
        "case_id": row.case_id,
        "workflow_id": row.workflow_id,
        "requested_by": row.requested_by,
        "requested_from": row.requested_from,
        "type": row.type,
        "context": json_loads(row.context_json, {}),
        "allowed_actions": json_loads(row.allowed_actions_json, []),
        "status": row.status,
        "decision": json_loads(row.decision_json, None),
        "resolved_by": row.resolved_by,
        "created_at": row.created_at.isoformat(),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def _allow_any_resolver() -> bool:
    return os.environ.get("ALLOW_ANY_RESOLVER") == "1"


def create_task(session: Session, task: HumanTask) -> dict[str, Any]:
    """Persist a task row and emit ``HUMAN_TASK_CREATED``."""
    if session.get(HumanTaskRow, task.human_task_id) is not None:
        raise BackendError(
            409, "TASK_EXISTS", f"Task {task.human_task_id!r} already exists"
        )
    row = HumanTaskRow(
        human_task_id=task.human_task_id,
        case_id=task.case_id,
        workflow_id=task.workflow_id,
        requested_by=task.requested_by,
        requested_from=task.requested_from,
        type=str(task.type),
        context_json=json_dumps(task.context),
        allowed_actions_json=json_dumps(task.allowed_actions),
        status=task.status,
        decision_json=json_dumps(task.decision) if task.decision is not None else None,
        resolved_by=task.resolved_by,
        created_at=task.created_at,
        resolved_at=task.resolved_at,
    )
    session.add(row)
    emit_event(
        session,
        task.case_id,
        task.workflow_id,
        task.requested_by,
        EventType.HUMAN_TASK_CREATED,
        {"human_task_id": task.human_task_id, "type": str(task.type)},
    )
    session.commit()
    return _task_dict(row)


def get_task(session: Session, human_task_id: str) -> dict[str, Any]:
    """Return a single task by id."""
    row = session.get(HumanTaskRow, human_task_id)
    if row is None:
        raise BackendError(404, "NOT_FOUND", f"Task {human_task_id!r} not found")
    return _task_dict(row)


def list_tasks(session: Session, case_id: str | None) -> list[dict[str, Any]]:
    """List tasks, optionally filtered to one case."""
    stmt = select(HumanTaskRow).order_by("created_at")
    if case_id is not None:
        stmt = stmt.where(HumanTaskRow.case_id == case_id)
    return [_task_dict(row) for row in session.exec(stmt).all()]


def decide(
    session: Session,
    human_task_id: str,
    decision: dict[str, Any],
    resolved_by: str,
) -> dict[str, Any]:
    """Record a human decision and resume the linked workflow (SPEC §15)."""
    row = session.get(HumanTaskRow, human_task_id)
    if row is None:
        raise BackendError(404, "NOT_FOUND", f"Task {human_task_id!r} not found")
    if row.status != "open":
        raise BackendError(
            409, "TASK_NOT_OPEN", f"Task {human_task_id!r} is already resolved"
        )
    if not _allow_any_resolver() and resolved_by != row.requested_from:
        raise BackendError(
            403,
            "UNAUTHORIZED_APPROVER",
            f"Only {row.requested_from!r} may resolve task {human_task_id!r}",
        )

    row.decision_json = json_dumps(decision)
    row.status = "resolved"
    row.resolved_by = resolved_by
    row.resolved_at = utcnow()

    event_type = (
        EventType.APPROVAL_REJECTED
        if decision.get("decision") == "reject"
        else EventType.APPROVAL_GRANTED
    )
    emit_event(
        session,
        row.case_id,
        row.workflow_id,
        "human",
        event_type,
        {"human_task_id": human_task_id, "decision": decision},
    )

    workflow = session.get(WorkflowRun, row.workflow_id)
    if workflow is not None and workflow.status == WorkflowState.WAITING_FOR_HUMAN.value:
        validate_transition(WorkflowState.WAITING_FOR_HUMAN, WorkflowState.RUNNING)
        workflow.status = WorkflowState.RUNNING.value
        workflow.updated_at = utcnow()

    session.commit()
    return _task_dict(row)
