"""Round-trip the protocol models against the SPEC §12 wire examples."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentlab.sdk.protocols import (
    Event,
    HumanTask,
    HumanTaskType,
    WorkflowOutcome,
    WorkflowRequest,
    WorkflowState,
    WorkflowStatus,
)

WORKFLOW_REQUEST_JSON = """{
  "workflow_id": "WF-D-42",
  "case_id": "ONB-42",
  "goal": "employee_device_ready",
  "employee_id": "E42",
  "context": {"start_date": "2026-09-07"}
}"""

WORKFLOW_STATUS_JSON = """{
  "workflow_id": "WF-D-42",
  "status": "blocked",
  "blockers": [{"code": "NO_INVENTORY", "description": "Standard device unavailable"}]
}"""

WORKFLOW_OUTCOME_JSON = """{
  "workflow_id": "WF-D-42",
  "status": "completed",
  "verified": true
}"""


def test_workflow_request_round_trip() -> None:
    model = WorkflowRequest.model_validate_json(WORKFLOW_REQUEST_JSON)
    assert model.model_dump(mode="json") == json.loads(WORKFLOW_REQUEST_JSON)


def test_workflow_status_round_trip() -> None:
    model = WorkflowStatus.model_validate_json(WORKFLOW_STATUS_JSON)
    assert model.model_dump(mode="json") == json.loads(WORKFLOW_STATUS_JSON)


def test_workflow_outcome_round_trip() -> None:
    model = WorkflowOutcome.model_validate_json(WORKFLOW_OUTCOME_JSON)
    assert model.model_dump(mode="json") == json.loads(WORKFLOW_OUTCOME_JSON)


def test_workflow_request_forbids_extra_field() -> None:
    with pytest.raises(ValidationError):
        WorkflowRequest.model_validate(
            {
                "workflow_id": "WF-D-42",
                "case_id": "ONB-42",
                "goal": "employee_device_ready",
                "employee_id": "E42",
                "context": {},
                "bogus": "nope",
            }
        )


def test_workflow_state_values() -> None:
    assert {state.value for state in WorkflowState} == {
        "acknowledged",
        "running",
        "blocked",
        "waiting_for_human",
        "failed",
        "completed",
    }


def test_event_to_dict_iso_format() -> None:
    event = Event(
        ts=datetime(2026, 9, 7, 9, 30, 0, tzinfo=UTC),
        case_id="ONB-42",
        workflow_id=None,
        actor="device-agent",
        type="TOOL_CALL",
        payload={"tool": "check_inventory"},
    )
    assert event.to_dict()["ts"] == "2026-09-07T09:30:00+00:00"


def test_human_task_defaults() -> None:
    task = HumanTask(
        human_task_id="HT-1",
        case_id="ONB-42",
        workflow_id="WF-D-42",
        requested_by="device-agent",
        requested_from="sre-manager",
        type=HumanTaskType.APPROVAL,
        context={},
        allowed_actions=["approve", "reject"],
        status="open",
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    assert task.decision is None
    assert task.resolved_by is None
    assert task.resolved_at is None
