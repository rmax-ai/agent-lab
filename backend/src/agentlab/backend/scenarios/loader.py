"""Scenario YAML loader (SPEC §16, DEC-15).

Reads a scenario YAML file into a validated :class:`Scenario`. Schema
violations, missing files, and DEC-05-illegal fault targets raise
:class:`ScenarioConfigError`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentlab.backend.scenarios.faults import validate_fault_target
from agentlab.backend.scenarios.models import Scenario, ScenarioConfigError


def load_scenario(path: str | Path) -> Scenario:
    """Load, validate, and normalize a scenario YAML file.

    Args:
        path: Filesystem path to the ``.yaml`` scenario definition.

    Returns:
        The validated :class:`Scenario`.

    Raises:
        ScenarioConfigError: If the file is missing, the YAML is malformed,
            the document is not a mapping, the schema is invalid, or a fault
            targets a read tool (DEC-05).
    """
    scenario_path = Path(path)
    if not scenario_path.is_file():
        raise ScenarioConfigError(f"scenario file not found: {scenario_path}")

    try:
        raw: Any = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioConfigError(f"invalid YAML in {scenario_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ScenarioConfigError(f"scenario {scenario_path} must be a YAML mapping")

    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as exc:
        raise ScenarioConfigError(f"invalid scenario {scenario_path}: {exc}") from exc

    for fault in scenario.faults:
        validate_fault_target(fault.tool)

    return scenario
