"""Event type vocabulary (SPEC §23 trace timeline).

Pure vocabulary: no logic beyond the enumeration. The trace timeline is a
filtered query over the Event Store's append-only rows.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    """Canonical event type strings written to the Event Store (SPEC §23)."""

    CASE_CREATED = "CASE_CREATED"
    WORKFLOW_DELEGATED = "WORKFLOW_DELEGATED"
    WORKFLOW_ACKNOWLEDGED = "WORKFLOW_ACKNOWLEDGED"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    KNOWLEDGE_READ = "KNOWLEDGE_READ"
    BLOCKER_CREATED = "BLOCKER_CREATED"
    HUMAN_TASK_CREATED = "HUMAN_TASK_CREATED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    OUTCOME_VERIFIED = "OUTCOME_VERIFIED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    ESCALATED = "ESCALATED"
