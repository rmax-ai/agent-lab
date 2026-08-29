"""Deterministic TeamAgent tests: no real model calls (PYTHON_DEVELOPMENT.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from agentlab.sdk.agent import TeamAgent
from agentlab.sdk.knowledge import MarkdownKnowledgeProvider


async def echo(text: str) -> str:
    """Echo back the given text."""
    return text


async def test_arun_returns_canned_model_response(tmp_path: Path) -> None:
    knowledge = MarkdownKnowledgeProvider(str(tmp_path))
    agent = TeamAgent(
        id="device-agent",
        goal="employee_device_ready",
        instructions="You are the device agent.",
        knowledge=knowledge,
        tools=[echo],
    )

    async def canned(callback_context: Any, llm_request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="MOCKED")])
        )

    agent.agent.before_model_callback = canned

    assert await agent.arun("hello") == "MOCKED"


def test_agent_name_normalization() -> None:
    assert TeamAgent._normalize_name("device-agent") == "device_agent"
