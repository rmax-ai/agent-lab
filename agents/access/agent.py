"""Access agent factory (SPEC §5, DEC-11, DEC-17).

``build_access_agent`` assembles the second domain agent — the Epic B
horizontal-replication proof that the Device-agent pattern transfers to a new
domain with a different HITL shape (approval-gated groups). It mirrors
``build_device_agent`` exactly: an id, a goal, instructions, a Markdown
knowledge provider, three MockWorld tools, and an explicit model. Everything
the model needs to reason about the access workflow lives in
``instructions.md`` and the knowledge corpus; the tools are the only way to
touch world state.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentlab.sdk import MarkdownKnowledgeProvider, TeamAgent

from .tools import access

_HERE = Path(__file__).resolve().parent
_DEFAULT_KNOWLEDGE_DIR = _HERE / ".." / ".." / "knowledge" / "access"
_INSTRUCTIONS = _HERE / "instructions.md"

_TOOLS = [
    access.get_access_summary,
    access.request_group_access,
    access.list_access_requests,
]


def _knowledge_dir() -> str:
    """Resolve the knowledge directory from env, else the repo-root default."""
    configured = os.environ.get("AGENTLAB_ACCESS_KNOWLEDGE")
    return configured or str(_DEFAULT_KNOWLEDGE_DIR)


def build_access_agent() -> TeamAgent:
    """Build the Access agent with an explicit, env-configurable model."""
    return TeamAgent(
        id="access-agent",
        goal="employee_access_ready",
        instructions=str(_INSTRUCTIONS),
        knowledge=MarkdownKnowledgeProvider(_knowledge_dir()),
        tools=_TOOLS,
        model=os.environ.get("AGENTLAB_MODEL"),
    )


__all__ = ["build_access_agent"]
