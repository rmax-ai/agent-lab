"""Applications agent factory (SPEC §5, DEC-11, DEC-17).

``build_applications_agent`` assembles the fourth domain agent — Epic B's
third horizontal-replication proof that the Access/Device-agent pattern
transfers to a full mutator domain: the applications world surface exposes a
truthful read AND an idempotent provisioning route, so this agent both
provisions and verifies (unlike Systems, whose surface is read-only and
HITL-only). It mirrors ``build_access_agent`` exactly: an id, a goal,
instructions, a Markdown knowledge provider, four tools, and an explicit
model. Everything the model needs to reason about the applications workflow
lives in ``instructions.md`` and the knowledge corpus; the tools are the
only way to touch world state.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentlab.sdk import MarkdownKnowledgeProvider, TeamAgent

from .tools import applications

_HERE = Path(__file__).resolve().parent
_DEFAULT_KNOWLEDGE_DIR = _HERE / ".." / ".." / "knowledge" / "applications"
_INSTRUCTIONS = _HERE / "instructions.md"

_TOOLS = [
    applications.get_required_applications,
    applications.get_application_access,
    applications.provision_application,
    applications.verify_application_access,
]


def _knowledge_dir() -> str:
    """Resolve the knowledge directory from env, else the repo-root default."""
    configured = os.environ.get("AGENTLAB_APPLICATIONS_KNOWLEDGE")
    return configured or str(_DEFAULT_KNOWLEDGE_DIR)


def build_applications_agent() -> TeamAgent:
    """Build the Applications agent with an explicit, env-configurable model."""
    return TeamAgent(
        id="applications-agent",
        goal="employee_applications_ready",
        instructions=str(_INSTRUCTIONS),
        knowledge=MarkdownKnowledgeProvider(_knowledge_dir()),
        tools=_TOOLS,
        model=os.environ.get("AGENTLAB_MODEL"),
    )


__all__ = ["build_applications_agent"]
