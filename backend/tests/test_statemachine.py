"""State machine transition table tests (SPEC §12)."""

from __future__ import annotations

import pytest

from agentlab.backend.statemachine import (
    TRANSITIONS,
    IllegalTransitionError,
    validate_transition,
)
from agentlab.sdk.protocols import WorkflowState

LEGAL_TRANSITIONS: list[tuple[WorkflowState, WorkflowState]] = [
    (WorkflowState.ACKNOWLEDGED, WorkflowState.RUNNING),
    (WorkflowState.RUNNING, WorkflowState.BLOCKED),
    (WorkflowState.RUNNING, WorkflowState.WAITING_FOR_HUMAN),
    (WorkflowState.RUNNING, WorkflowState.FAILED),
    (WorkflowState.RUNNING, WorkflowState.COMPLETED),
    (WorkflowState.BLOCKED, WorkflowState.RUNNING),
    (WorkflowState.WAITING_FOR_HUMAN, WorkflowState.RUNNING),
    (WorkflowState.FAILED, WorkflowState.RUNNING),
]

ILLEGAL_TRANSITIONS: list[tuple[WorkflowState, WorkflowState]] = [
    (WorkflowState.ACKNOWLEDGED, WorkflowState.ACKNOWLEDGED),
    (WorkflowState.ACKNOWLEDGED, WorkflowState.BLOCKED),
    (WorkflowState.ACKNOWLEDGED, WorkflowState.WAITING_FOR_HUMAN),
    (WorkflowState.ACKNOWLEDGED, WorkflowState.FAILED),
    (WorkflowState.ACKNOWLEDGED, WorkflowState.COMPLETED),
    (WorkflowState.RUNNING, WorkflowState.ACKNOWLEDGED),
    (WorkflowState.RUNNING, WorkflowState.RUNNING),
    (WorkflowState.BLOCKED, WorkflowState.BLOCKED),
    (WorkflowState.BLOCKED, WorkflowState.WAITING_FOR_HUMAN),
    (WorkflowState.BLOCKED, WorkflowState.FAILED),
    (WorkflowState.BLOCKED, WorkflowState.COMPLETED),
    (WorkflowState.WAITING_FOR_HUMAN, WorkflowState.WAITING_FOR_HUMAN),
    (WorkflowState.WAITING_FOR_HUMAN, WorkflowState.BLOCKED),
    (WorkflowState.WAITING_FOR_HUMAN, WorkflowState.FAILED),
    (WorkflowState.WAITING_FOR_HUMAN, WorkflowState.COMPLETED),
    (WorkflowState.FAILED, WorkflowState.FAILED),
    (WorkflowState.FAILED, WorkflowState.BLOCKED),
    (WorkflowState.FAILED, WorkflowState.COMPLETED),
    (WorkflowState.COMPLETED, WorkflowState.ACKNOWLEDGED),
    (WorkflowState.COMPLETED, WorkflowState.RUNNING),
    (WorkflowState.COMPLETED, WorkflowState.BLOCKED),
    (WorkflowState.COMPLETED, WorkflowState.WAITING_FOR_HUMAN),
    (WorkflowState.COMPLETED, WorkflowState.FAILED),
    (WorkflowState.COMPLETED, WorkflowState.COMPLETED),
]


@pytest.mark.parametrize(("from_state", "to_state"), LEGAL_TRANSITIONS)
def test_legal_transitions_pass(
    from_state: WorkflowState,
    to_state: WorkflowState,
) -> None:
    validate_transition(from_state, to_state)  # must not raise


@pytest.mark.parametrize(("from_state", "to_state"), ILLEGAL_TRANSITIONS)
def test_illegal_transitions_raise_value_error(
    from_state: WorkflowState,
    to_state: WorkflowState,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        validate_transition(from_state, to_state)
    assert str(excinfo.value).startswith("illegal_transition")
    assert from_state.value in str(excinfo.value)
    assert to_state.value in str(excinfo.value)


def test_illegal_transition_is_value_error_subclass() -> None:
    assert issubclass(IllegalTransitionError, ValueError)


def test_completed_is_terminal() -> None:
    for state in WorkflowState:
        with pytest.raises(ValueError):
            validate_transition(WorkflowState.COMPLETED, state)


def test_transition_table_marks_completed_terminal() -> None:
    assert TRANSITIONS[WorkflowState.COMPLETED] == set()
