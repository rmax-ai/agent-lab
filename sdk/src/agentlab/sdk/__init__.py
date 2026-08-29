"""Agent Lab SDK — protocols, transport, knowledge, TeamAgent wrapper.

The public surface teams build against. Everything is a swappable boundary:
protocols over the wire, ``KnowledgeProvider`` over Markdown, ``AgentTransport``
over WebSockets, and ``TeamAgent`` over Google ADK.
"""

from agentlab.sdk.agent import TeamAgent
from agentlab.sdk.client import AgentLabClient, AgentLabError
from agentlab.sdk.events import EventType
from agentlab.sdk.knowledge import KnowledgeDocument, KnowledgeProvider, MarkdownKnowledgeProvider
from agentlab.sdk.protocols import (
    Blocker,
    Event,
    HumanTask,
    HumanTaskType,
    WorkflowOutcome,
    WorkflowRequest,
    WorkflowState,
    WorkflowStatus,
)
from agentlab.sdk.transport import AgentLabTransport, AgentTransport

__version__ = "0.1.0"

__all__ = [
    "AgentLabClient",
    "AgentLabError",
    "AgentLabTransport",
    "AgentTransport",
    "Blocker",
    "Event",
    "EventType",
    "HumanTask",
    "HumanTaskType",
    "KnowledgeDocument",
    "KnowledgeProvider",
    "MarkdownKnowledgeProvider",
    "TeamAgent",
    "WorkflowOutcome",
    "WorkflowRequest",
    "WorkflowState",
    "WorkflowStatus",
]
