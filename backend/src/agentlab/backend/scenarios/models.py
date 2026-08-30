"""Pydantic models for the scenario YAML schema (SPEC §16, DEC-15).

Flat, snake_case models (DEC-16). A scenario controls the world, never the
agents: ``initial_state`` is passed verbatim to ``POST /simulation/load``,
``events`` mutate world fields on a timed schedule, and ``faults`` inject
DEC-05 tool faults into declared mutation tools only.

Timing bounds default to the central :data:`agentlab.backend.constants`
values and may be overridden per scenario.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentlab.backend.constants import (
    MAX_DELEGATION_DEPTH,
    MAX_RETRIES,
    TOOL_TIMEOUT_SECONDS,
)

# DEC-05: the four injectable tool-fault kinds (SPEC §17.2).
FaultKind = Literal["timeout", "http_500", "stale", "success_without_state_change"]

# Efficiency-category budget for the size of a single run's timeline (SPEC §24).
DEFAULT_TIMELINE_BUDGET = 25


class ScenarioConfigError(ValueError):
    """A scenario definition violates the schema or the DEC-05 fault rules."""


class ScenarioEvent(BaseModel):
    """A timed world mutation (SPEC §16 ``events``)."""

    model_config = ConfigDict(extra="forbid")

    at: float = Field(ge=0)
    mutate: dict[str, Any] = Field(default_factory=dict)


class ScenarioFault(BaseModel):
    """A timed tool fault against a declared mutation tool (SPEC §17.2)."""

    model_config = ConfigDict(extra="forbid")

    at: float = Field(ge=0)
    tool: str
    kind: FaultKind


class ScenarioExpected(BaseModel):
    """Deterministic evaluation assertions (SPEC §16 ``expected``)."""

    model_config = ConfigDict(extra="forbid")

    required_events: list[str] = Field(default_factory=list)
    allowed_final_states: list[str] = Field(default_factory=list)
    forbidden_events: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    """The full scenario definition (SPEC §16)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    initial_state: dict[str, Any] = Field(default_factory=dict)
    events: list[ScenarioEvent] = Field(default_factory=list)
    faults: list[ScenarioFault] = Field(default_factory=list)
    expected: ScenarioExpected

    # Optional per-scenario overrides of the central runtime bounds (DEC-08)
    # and the evaluation pass threshold (SPEC §24).
    max_retries: int = MAX_RETRIES
    max_delegation_depth: int = MAX_DELEGATION_DEPTH
    tool_timeout_seconds: float = TOOL_TIMEOUT_SECONDS
    timeline_budget: int = DEFAULT_TIMELINE_BUDGET
    pass_threshold: float | None = None
