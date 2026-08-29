"""Backend-owned SQLModel tables (SPEC §11/§12/§15/§23).

The backend shares one SQLite file with MockWorld but owns only these four
tables. MockWorld owns the world-domain tables. No foreign keys cross that
boundary; referential integrity is validated in service code. JSON-shaped
fields are stored as TEXT with the ``json_dumps``/``json_loads`` helpers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Return the current time as an aware UTC ``datetime``."""
    return datetime.now(UTC)


def json_dumps(value: Any) -> str:
    """Serialize a JSON-shaped value to its TEXT-column representation."""
    return json.dumps(value)


def json_loads(text: str | None, default: Any) -> Any:
    """Deserialize a TEXT column, falling back to ``default`` when absent."""
    if text is None or text == "":
        return default
    return json.loads(text)


class OnboardingCase(SQLModel, table=True):
    """A single onboarding case (SPEC §11)."""

    __tablename__ = "onboarding_cases"

    case_id: str = Field(primary_key=True)
    employee_id: str
    status: str = "open"
    context_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


class WorkflowRun(SQLModel, table=True):
    """A delegated workflow run against the common contract (SPEC §12)."""

    __tablename__ = "workflow_runs"

    workflow_id: str = Field(primary_key=True)
    case_id: str
    goal: str
    employee_id: str
    agent_id: str
    status: str = "acknowledged"
    blockers_json: str = "[]"
    verified: bool = False
    retry_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class EventRow(SQLModel, table=True):
    """An append-only trace row (SPEC §23)."""

    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow)
    case_id: str
    workflow_id: str | None = None
    actor: str
    type: str
    payload_json: str = "{}"


class HumanTaskRow(SQLModel, table=True):
    """A persisted human-in-the-loop task (SPEC §15)."""

    __tablename__ = "human_tasks"

    human_task_id: str = Field(primary_key=True)
    case_id: str
    workflow_id: str
    requested_by: str
    requested_from: str
    type: str
    context_json: str = "{}"
    allowed_actions_json: str = "[]"
    status: str = "open"
    decision_json: str | None = None
    resolved_by: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None


# The complete set of tables the backend owns. ``create_all`` iterates this list
# so MockWorld-owned tables are never touched by the backend.
BACKEND_MODELS: list[type[SQLModel]] = [
    OnboardingCase,
    WorkflowRun,
    EventRow,
    HumanTaskRow,
]
