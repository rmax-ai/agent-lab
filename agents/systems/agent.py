"""Systems agent factory (SPEC §5, DEC-11, DEC-17).

``build_systems_agent`` assembles the third domain agent — Epic B's second
horizontal-replication proof that the Access/Device-agent pattern transfers to
a domain with a genuinely different provisioning model: the systems world
surface is READ-ONLY, so provisioning goes through the backend HumanTask flow
(IT ticket) and accounts materialize via world-state changes the agent
discovers through truthful reads. It mirrors ``build_access_agent`` exactly:
an id, a goal, instructions, a Markdown knowledge provider, four tools, and
an explicit model. Everything the model needs to reason about the systems
workflow lives in ``instructions.md`` and the knowledge corpus; the tools are
the only way to touch world state (reads) and the provisioning task flow.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentlab.sdk import MarkdownKnowledgeProvider, TeamAgent

from .tools import systems

_HERE = Path(__file__).resolve().parent
_DEFAULT_KNOWLEDGE_DIR = _HERE / ".." / ".." / "knowledge" / "systems"
_INSTRUCTIONS = _HERE / "instructions.md"

_TOOLS = [
    systems.get_required_systems,
    systems.get_account_status,
    systems.provision_account,
    systems.verify_account,
]


def _knowledge_dir() -> str:
    """Resolve the knowledge directory from env, else the repo-root default."""
    configured = os.environ.get("AGENTLAB_SYSTEMS_KNOWLEDGE")
    return configured or str(_DEFAULT_KNOWLEDGE_DIR)


def build_systems_agent() -> TeamAgent:
    """Build the Systems agent with an explicit, env-configurable model."""
    return TeamAgent(
        id="systems-agent",
        goal="employee_systems_ready",
        instructions=str(_INSTRUCTIONS),
        knowledge=MarkdownKnowledgeProvider(_knowledge_dir()),
        tools=_TOOLS,
        model=os.environ.get("AGENTLAB_MODEL"),
    )


__all__ = ["build_systems_agent"]
