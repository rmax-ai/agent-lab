"""Workflow engine service (SPEC §11/§12).

Every transition is validated against the central state machine and recorded as
an event plus an atomic ``workflow_runs`` row update (PATTERNS §1). Ownership is
enforced here: only the owning agent may ack, report, complete, or fail a
workflow.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from agentlab.backend.constants import MAX_RETRIES
from agentlab.backend.errors import BackendError
from agentlab.backend.events import emit_event
from agentlab.backend.models import WorkflowRun, json_dumps, json_loads, utcnow
from agentlab.backend.statemachine import validate_transition
from agentlab.sdk.events import EventType
from agentlab.sdk.protocols import (
    Blocker,
    WorkflowRequest,
    WorkflowState,
    WorkflowStatus,
)

_WORKFLOW_STATUS_TYPE = "WORKFLOW_STATUS"


def _workflow_summary(run: WorkflowRun) -> dict[str, Any]:
    return {
        "workflow_id": run.workflow_id,
        "case_id": run.case_id,
        "goal": run.goal,
        "employee_id": run.employee_id,
        "agent_id": run.agent_id,
        "status": run.status,
        "blockers": json_loads(run.blockers_json, []),
        "verified": run.verified,
        "retry_count": run.retry_count,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _get_run(session: Session, workflow_id: str) -> WorkflowRun:
    run = session.get(WorkflowRun, workflow_id)
    if run is None:
        raise BackendError(404, "NOT_FOUND", f"Workflow {workflow_id!r} not found")
    return run


def _require_owner(run: WorkflowRun, agent_id: str) -> None:
    if run.agent_id != agent_id:
        raise BackendError(
            403,
            "FORBIDDEN",
            f"Agent {agent_id!r} does not own workflow {run.workflow_id!r}",
        )


def start_workflow(
    session: Session,
    request: WorkflowRequest,
    target_agent_id: str,
    delegator: str = "coordinator",
) -> dict[str, Any]:
    """Create an ``acknowledged`` workflow row and emit ``WORKFLOW_DELEGATED``."""
    if session.get(WorkflowRun, request.workflow_id) is not None:
        raise BackendError(
            409, "WORKFLOW_EXISTS", f"Workflow {request.workflow_id!r} already exists"
        )
    now = utcnow()
    run = WorkflowRun(
        workflow_id=request.workflow_id,
        case_id=request.case_id,
        goal=request.goal,
        employee_id=request.employee_id,
        agent_id=target_agent_id,
        status=WorkflowState.ACKNOWLEDGED.value,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    emit_event(
        session,
        request.case_id,
        request.workflow_id,
        delegator,
        EventType.WORKFLOW_DELEGATED,
        {"goal": request.goal, "target_agent_id": target_agent_id},
    )
    session.commit()
    return _workflow_summary(run)


def acknowledge(session: Session, workflow_id: str, agent_id: str) -> dict[str, Any]:
    """Transition ``ACKNOWLEDGED → RUNNING`` and emit ``WORKFLOW_ACKNOWLEDGED``."""
    run = _get_run(session, workflow_id)
    _require_owner(run, agent_id)
    validate_transition(WorkflowState(run.status), WorkflowState.RUNNING)
    run.status = WorkflowState.RUNNING.value
    run.updated_at = utcnow()
    emit_event(
        session,
        run.case_id,
        workflow_id,
        agent_id,
        EventType.WORKFLOW_ACKNOWLEDGED,
        {"workflow_id": workflow_id},
    )
    session.commit()
    return _workflow_summary(run)


def report_status(
    session: Session,
    workflow_id: str,
    agent_id: str,
    status: WorkflowStatus,
) -> dict[str, Any]:
    """Validate and persist a status report, emitting the matching events.

    Reporting the state the workflow is already in is an idempotent no-op
    transition (re-porting blockers, for example). ``BLOCKED`` emits one
    ``BLOCKER_CREATED`` per blocker; ``WAITING_FOR_HUMAN`` emits nothing here
    because the task service owns ``HUMAN_TASK_CREATED``; every other state
    emits a generic ``WORKFLOW_STATUS`` event.
    """
    run = _get_run(session, workflow_id)
    _require_owner(run, agent_id)
    current = WorkflowState(run.status)
    if status.status != current:
        validate_transition(current, status.status)

    blockers = [blocker.model_dump() for blocker in status.blockers]
    run.status = status.status.value
    run.blockers_json = json_dumps(blockers)
    run.updated_at = utcnow()

    if status.status == WorkflowState.BLOCKED:
        for blocker in status.blockers:
            emit_event(
                session,
                run.case_id,
                workflow_id,
                agent_id,
                EventType.BLOCKER_CREATED,
                blocker.model_dump(),
            )
    elif status.status != WorkflowState.WAITING_FOR_HUMAN:
        emit_event(
            session,
            run.case_id,
            workflow_id,
            agent_id,
            _WORKFLOW_STATUS_TYPE,
            {
                "workflow_id": workflow_id,
                "status": status.status.value,
                "blockers": blockers,
            },
        )
    session.commit()
    return _workflow_summary(run)


def complete_workflow(
    session: Session,
    workflow_id: str,
    agent_id: str,
    verified: bool,
) -> dict[str, Any]:
    """Transition to ``COMPLETED``, which requires ``verified`` (SPEC §19)."""
    run = _get_run(session, workflow_id)
    _require_owner(run, agent_id)
    if not verified:
        raise BackendError(
            409,
            "VERIFICATION_REQUIRED",
            f"Workflow {workflow_id!r} requires verified=true to complete",
        )
    validate_transition(WorkflowState(run.status), WorkflowState.COMPLETED)
    run.status = WorkflowState.COMPLETED.value
    run.verified = True
    run.updated_at = utcnow()
    emit_event(
        session,
        run.case_id,
        workflow_id,
        agent_id,
        EventType.OUTCOME_VERIFIED,
        {"workflow_id": workflow_id, "verified": True},
    )
    session.commit()
    return _workflow_summary(run)


def fail_workflow(
    session: Session,
    workflow_id: str,
    agent_id: str,
    reason: str,
) -> dict[str, Any]:
    """Fail a workflow, retrying while under ``MAX_RETRIES`` then settling on ``FAILED``."""
    run = _get_run(session, workflow_id)
    _require_owner(run, agent_id)
    current = WorkflowState(run.status)
    validate_transition(current, WorkflowState.FAILED)

    if run.retry_count < MAX_RETRIES:
        run.retry_count += 1
        validate_transition(WorkflowState.FAILED, WorkflowState.RUNNING)
        new_status = WorkflowState.RUNNING
        retry = True
    else:
        new_status = WorkflowState.FAILED
        retry = False

    run.status = new_status.value
    run.updated_at = utcnow()
    emit_event(
        session,
        run.case_id,
        workflow_id,
        agent_id,
        EventType.WORKFLOW_FAILED,
        {
            "workflow_id": workflow_id,
            "reason": reason,
            "retry": retry,
            "retry_count": run.retry_count,
        },
    )
    session.commit()
    return _workflow_summary(run)


def mark_blocked(
    session: Session,
    workflow_id: str,
    agent_id: str,
    blockers: list[Blocker],
) -> dict[str, Any]:
    """Convenience wrapper for reporting ``BLOCKED`` with blockers."""
    blocked = WorkflowStatus(
        workflow_id=workflow_id,
        status=WorkflowState.BLOCKED,
        blockers=blockers,
    )
    return report_status(session, workflow_id, agent_id, blocked)


def mark_waiting_for_human(
    session: Session,
    workflow_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """Convenience wrapper for reporting ``WAITING_FOR_HUMAN``."""
    waiting = WorkflowStatus(
        workflow_id=workflow_id,
        status=WorkflowState.WAITING_FOR_HUMAN,
        blockers=[],
    )
    return report_status(session, workflow_id, agent_id, waiting)


def get_workflow(session: Session, workflow_id: str) -> dict[str, Any]:
    """Return a workflow summary."""
    return _workflow_summary(_get_run(session, workflow_id))
