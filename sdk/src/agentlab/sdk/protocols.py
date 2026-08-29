"""Protocol models for the Agent Lab workflow contract (SPEC §12/§15/§23).

Flat Pydantic v2 schemas (DEC-16): no nested model-of-model lists. Field names
are verbatim snake_case from SPEC.md. ``context`` on :class:`WorkflowRequest`
stays open by design; every other model forbids extra fields.

Wire example (SPEC §12)::

    {
      "workflow_id": "WF-D-42",
      "status": "blocked",
      "blockers": [{"code": "NO_INVENTORY", "description": "Standard device unavailable"}]
    }
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowRequest(BaseModel):
    """A coordinator delegates an *outcome* (never a domain action) with this model."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    case_id: str
    goal: str
    employee_id: str
    context: dict[str, Any]


class Blocker(BaseModel):
    """A typed business rejection (SPEC §12). Codes reuse the blocker vocabulary."""

    model_config = ConfigDict(extra="forbid")

    code: str
    description: str


class WorkflowState(StrEnum):
    """Workflow lifecycle states (SPEC §12).

    Member names are the canonical lifecycle nouns. Values are lowercase so the
    wire format matches the SPEC §12 examples exactly (``"status": "blocked"``).
    """

    ACKNOWLEDGED = "acknowledged"
    RUNNING = "running"
    BLOCKED = "blocked"
    WAITING_FOR_HUMAN = "waiting_for_human"
    FAILED = "failed"
    COMPLETED = "completed"


class WorkflowStatus(BaseModel):
    """Current state of a delegated workflow (SPEC §12)."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    status: WorkflowState
    blockers: list[Blocker] = Field(default_factory=list)


class WorkflowOutcome(BaseModel):
    """Final outcome of a workflow (SPEC §12). ``COMPLETED`` requires ``verified``."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    status: WorkflowState
    verified: bool


class Event(BaseModel):
    """Append-only trace record (SPEC §23)."""

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    case_id: str
    workflow_id: str | None
    actor: str
    type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict with an ISO-8601 timestamp."""
        return {
            "ts": self.ts.isoformat(),
            "case_id": self.case_id,
            "workflow_id": self.workflow_id,
            "actor": self.actor,
            "type": self.type,
            "payload": self.payload,
        }


class HumanTaskType(StrEnum):
    """Human-in-the-loop task categories (SPEC §15)."""

    APPROVAL = "APPROVAL"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    CONFLICT_RESOLUTION = "CONFLICT_RESOLUTION"
    EXCEPTION_HANDLING = "EXCEPTION_HANDLING"
    MANUAL_ACTION = "MANUAL_ACTION"


class HumanTask(BaseModel):
    """Persisted human-in-the-loop state (SPEC §15)."""

    model_config = ConfigDict(extra="forbid")

    human_task_id: str
    case_id: str
    workflow_id: str
    requested_by: str
    requested_from: str
    type: HumanTaskType
    context: dict[str, Any]
    allowed_actions: list[str]
    status: str
    decision: dict[str, Any] | None = None
    resolved_by: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
