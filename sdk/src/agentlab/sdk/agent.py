"""Team agent wrapper around a Google ADK ``LlmAgent`` (SPEC §5, DEC-17).

``TeamAgent`` owns the Agent Lab plumbing so a team's agent definition stays
small: an id, a goal, instructions (text or Markdown file), a knowledge
provider and a list of tools. The model is always explicit: constructor arg,
else ``AGENTLAB_MODEL``, else ``gemini-2.5-flash``.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from agentlab.sdk.knowledge import KnowledgeProvider

_DEFAULT_MODEL = "gemini-2.5-flash"

_NAME_PATTERN = re.compile(r"[^0-9A-Za-z_]")


class TeamAgent:
    """A swappable, ADK-backed domain agent (SPEC §5)."""

    def __init__(
        self,
        id: str,
        goal: str,
        instructions: str,
        knowledge: KnowledgeProvider,
        tools: list[Any],
        model: str | None = None,
    ) -> None:
        self.id = id
        self.goal = goal
        self.knowledge = knowledge
        self.model = model or os.environ.get("AGENTLAB_MODEL") or _DEFAULT_MODEL
        self._agent = LlmAgent(
            name=self._normalize_name(id),
            instruction=self._load_instructions(instructions),
            tools=tools,
            model=self.model,
        )

    @property
    def agent(self) -> LlmAgent:
        """The underlying ADK agent, for callback attachment and inspection."""
        return self._agent

    @staticmethod
    def _normalize_name(identifier: str) -> str:
        """Coerce an id into a valid Python identifier for ADK's ``name``."""
        name = _NAME_PATTERN.sub("_", identifier)
        if not name or name[0].isdigit():
            name = f"_{name}"
        return name

    @staticmethod
    def _load_instructions(instructions: str) -> str:
        if instructions.endswith(".md"):
            path = Path(instructions)
            if path.is_file():
                return path.read_text(encoding="utf-8")
        return instructions

    async def arun(self, user_input: str, session_id: str = "default") -> str:
        """Run one turn and return the concatenated final response text."""
        runner = InMemoryRunner(agent=self._agent, app_name="agentlab")
        runner.auto_create_session = True
        content = types.Content(role="user", parts=[types.Part(text=user_input)])
        chunks: list[str] = []
        async for event in runner.run_async(
            user_id=self.id,
            session_id=session_id,
            new_message=content,
        ):
            if event.partial:
                continue
            if event.is_final_response() and event.content:
                for part in event.content.parts or []:
                    if part.text:
                        chunks.append(part.text)
        return "".join(chunks)

    def run(self, user_input: str, session_id: str = "default") -> str:
        """Synchronous convenience wrapper around :meth:`arun`."""
        return asyncio.run(self.arun(user_input, session_id=session_id))
