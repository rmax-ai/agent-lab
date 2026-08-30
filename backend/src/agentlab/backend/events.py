"""Append-only event store (SPEC §23, PATTERNS §2).

One helper writes rows; the trace timeline is a filtered query ordered by
timestamp. Services call :func:`emit_event` inside their transaction and commit
as a unit with the state change that produced it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from agentlab.backend.errors import BackendError
from agentlab.backend.models import EventRow, OnboardingCase, WorkflowRun
from agentlab.sdk.protocols import Event


def _now() -> datetime:
    return datetime.now(UTC)


def emit_event(
    session: Session,
    case_id: str,
    workflow_id: str | None,
    actor: str,
    type: str | Any,
    payload: dict[str, Any],
) -> Event:
    """Persist an append-only event row and return the equivalent SDK model.

    The row is added to ``session`` but not committed; the caller commits it
    atomically with the state change that triggered the event.
    """
    event = Event(
        ts=_now(),
        case_id=case_id,
        workflow_id=workflow_id,
        actor=actor,
        type=str(type),
        payload=payload,
    )
    session.add(
        EventRow(
            ts=event.ts,
            case_id=case_id,
            workflow_id=workflow_id,
            actor=actor,
            type=str(type),
            payload_json=json.dumps(payload),
        )
    )
    return event


def record_agent_event(
    session: Session,
    *,
    agent_id: str,
    case_id: str,
    workflow_id: str | None,
    type: str | Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate and persist an agent-emitted event, then return the created row.

    Route-level referential validation (the case exists; a supplied workflow
    exists and belongs to that case) wraps the single writer helper, so
    :func:`emit_event` stays the ONLY path into the Event Store (SPEC §23).
    ``actor`` is always the authenticated agent id and ``ts`` is server-set;
    client-supplied values for either never reach the store.
    """
    if session.get(OnboardingCase, case_id) is None:
        raise BackendError(404, "NOT_FOUND", f"Case {case_id!r} not found")
    if workflow_id is not None:
        run = session.get(WorkflowRun, workflow_id)
        if run is None:
            raise BackendError(404, "NOT_FOUND", f"Workflow {workflow_id!r} not found")
        if run.case_id != case_id:
            raise BackendError(
                400,
                "WORKFLOW_CASE_MISMATCH",
                f"Workflow {workflow_id!r} does not belong to case {case_id!r}",
            )
    event = emit_event(session, case_id, workflow_id, agent_id, type, payload)
    session.commit()
    return event.to_dict()


def list_events(session: Session, case_id: str) -> list[Event]:
    """Return the case trace timeline ordered by timestamp ascending."""
    rows = session.exec(
        select(EventRow)
        .where(EventRow.case_id == case_id)
        .order_by("ts", "id")
    ).all()
    return [
        Event(
            ts=row.ts,
            case_id=row.case_id,
            workflow_id=row.workflow_id,
            actor=row.actor,
            type=row.type,
            payload=json.loads(row.payload_json),
        )
        for row in rows
    ]
