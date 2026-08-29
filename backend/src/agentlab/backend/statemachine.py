"""Workflow lifecycle transition table (SPEC §12, PATTERNS §1).

Single source of truth for what state a delegated workflow may move into next.
``COMPLETED`` is terminal; the *verified* requirement it carries is enforced in
the workflow engine (``complete_workflow``), not here.
"""

from __future__ import annotations

from agentlab.sdk.protocols import WorkflowState


class IllegalTransitionError(ValueError):
    """Raised when a proposed transition is not in the transition table."""

    def __init__(self, from_state: WorkflowState, to_state: WorkflowState) -> None:
        super().__init__(f"illegal_transition: {from_state.value} -> {to_state.value}")


TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.ACKNOWLEDGED: {WorkflowState.RUNNING},
    WorkflowState.RUNNING: {
        WorkflowState.BLOCKED,
        WorkflowState.WAITING_FOR_HUMAN,
        WorkflowState.FAILED,
        WorkflowState.COMPLETED,
    },
    WorkflowState.BLOCKED: {WorkflowState.RUNNING},
    WorkflowState.WAITING_FOR_HUMAN: {WorkflowState.RUNNING},
    WorkflowState.FAILED: {WorkflowState.RUNNING},
    WorkflowState.COMPLETED: set(),
}


def validate_transition(from_state: WorkflowState, to_state: WorkflowState) -> None:
    """Raise :class:`IllegalTransitionError` unless ``to_state`` is legal."""
    if to_state not in TRANSITIONS.get(from_state, set()):
        raise IllegalTransitionError(from_state, to_state)
