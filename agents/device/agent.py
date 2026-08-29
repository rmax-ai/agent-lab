"""Device agent factory (SPEC §5, DEC-11, DEC-17).

``build_device_agent`` assembles the reference vertical-slice domain agent: an
id, a goal, instructions, a Markdown knowledge provider, six MockWorld tools,
and an explicit model. Everything the model needs to reason about the device
workflow lives in ``instructions.md`` and the knowledge corpus; the tools are
the only way to touch world state.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentlab.sdk import MarkdownKnowledgeProvider, TeamAgent

from .tools import device

_HERE = Path(__file__).resolve().parent
_DEFAULT_KNOWLEDGE_DIR = _HERE / ".." / ".." / "knowledge" / "devices"
_INSTRUCTIONS = _HERE / "instructions.md"

_TOOLS = [
    device.get_employee_device_requirements,
    device.check_inventory,
    device.get_device_assignment,
    device.reserve_device,
    device.get_delivery_status,
    device.request_replacement,
]


def _knowledge_dir() -> str:
    """Resolve the knowledge directory from env, else the repo-root default."""
    configured = os.environ.get("AGENTLAB_DEVICE_KNOWLEDGE")
    return configured or str(_DEFAULT_KNOWLEDGE_DIR)


def build_device_agent() -> TeamAgent:
    """Build the Device agent with an explicit, env-configurable model."""
    return TeamAgent(
        id="device-agent",
        goal="employee_device_ready",
        instructions=str(_INSTRUCTIONS),
        knowledge=MarkdownKnowledgeProvider(_knowledge_dir()),
        tools=_TOOLS,
        model=os.environ.get("AGENTLAB_MODEL"),
    )


__all__ = ["build_device_agent"]
