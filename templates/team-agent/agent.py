"""Generic team-agent starter (SPEC §5).

Teams clone this scaffold and fill in the TODOs to build their own domain
agent. The platform wrapper (``TeamAgent``) owns the Agent Lab plumbing, so a
team only supplies: an id, a goal, instructions, a knowledge provider, and a
list of tools. The model is explicit and env-configurable (DEC-17).
"""

from __future__ import annotations

import os

from agentlab.sdk import MarkdownKnowledgeProvider, TeamAgent


def build_team_agent() -> TeamAgent:
    """Build a team agent from env vars and the local ``knowledge/`` directory.

    TODO: replace ``tools`` with your team's ADK function tools
          (see ``tools/example.py`` for the MockWorld HTTP pattern).
    TODO: drop a real Markdown corpus into ``knowledge/`` and give your agent
          an ``instructions.md`` that references those documents.
    """
    return TeamAgent(
        id=os.environ.get("AGENTLAB_AGENT_ID") or "team-agent",
        goal=os.environ.get("AGENTLAB_GOAL") or "team_goal",
        instructions="instructions.md",
        knowledge=MarkdownKnowledgeProvider("./knowledge"),
        tools=[],  # TODO: add your domain tools here
        model=os.environ.get("AGENTLAB_MODEL"),
    )
