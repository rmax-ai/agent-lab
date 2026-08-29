"""Deterministic tests for the Device agent factory (no network, no real LLM).

``build_device_agent`` must load the seven knowledge documents from
``knowledge/devices`` and produce an agent whose single turn is answered by a
canned ``before_model_callback`` response, proving no real model or network
call is required to run it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.agents.context import Context
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from agentlab.sdk import MarkdownKnowledgeProvider

from ..agent import build_device_agent

_REPO_ROOT = Path(__file__).resolve().parents[3]
_KNOWLEDGE_DIR = _REPO_ROOT / "knowledge" / "devices"


def _canned_response(callback_context: Context, llm_request: LlmRequest) -> LlmResponse:
    """Return a fixed model turn so the test never reaches a real model."""
    del callback_context, llm_request  # the canned turn ignores both
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text="DEVICE_READY")]),
        turn_complete=True,
    )


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("id", "device-agent"),
        ("goal", "employee_device_ready"),
    ],
)
def test_agent_identity(monkeypatch: pytest.MonkeyPatch, attr: str, expected: str) -> None:
    monkeypatch.setenv("AGENTLAB_DEVICE_KNOWLEDGE", str(_KNOWLEDGE_DIR))
    agent = build_device_agent()

    assert getattr(agent, attr) == expected


def test_agent_loads_all_knowledge_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTLAB_DEVICE_KNOWLEDGE", str(_KNOWLEDGE_DIR))
    agent = build_device_agent()

    knowledge = agent.knowledge
    assert isinstance(knowledge, MarkdownKnowledgeProvider)

    assert len(knowledge.documents) == 7
    assert {document.id for document in knowledge.documents} == {
        "README",
        "escalation",
        "exceptions",
        "inventory-substitution",
        "location-policy",
        "replacements",
        "standard-device-policy",
    }


async def test_scripted_turn_uses_canned_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTLAB_DEVICE_KNOWLEDGE", str(_KNOWLEDGE_DIR))
    agent = build_device_agent()
    agent.agent.before_model_callback = _canned_response

    response = await agent.arun("Make sure E42 has a device before their start date.")

    assert "DEVICE_READY" in response
