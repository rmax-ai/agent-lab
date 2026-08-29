"""Onboarding coordinator agent (SPEC §11, DEC-08, PATTERNS §5/§6).

The coordinator owns the ``employee_onboarding_ready`` outcome. It delegates
business outcomes to domain agents over the backend REST API and never touches
domain state itself: there are no reserve/replace/verify tools here and no
``agentlab.world`` import. Progress tracking reads case status and the case
event timeline; blockers are reconciled through the human-in-the-loop task
service; stalled work is escalated by failing the delegated workflow.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from agentlab.backend.constants import HITL_NO_RESPONSE_SECONDS
from agentlab.sdk import HumanTask, HumanTaskType, MarkdownKnowledgeProvider, TeamAgent

DEFAULT_BACKEND_URL = "http://localhost:8001"
DEFAULT_AGENT_ID = "onboarding-agent"
DEFAULT_GOAL = "employee_onboarding_ready"
OPS_LEAD = "ops-lead"

# Poll cadence for progress tracking. Never busy-loop: every status read is
# separated by ``asyncio.sleep(POLL_INTERVAL_SECONDS)``. Tests shrink this.
POLL_INTERVAL_SECONDS = 1.0

# Each domain maps to its delegated outcome goal plus the owning domain agent.
DOMAIN_GOALS: dict[str, tuple[str, str]] = {
    "device": ("employee_device_ready", "device-agent"),
    "access": ("employee_access_ready", "access-agent"),
    "systems": ("employee_systems_ready", "systems-agent"),
    "applications": ("employee_applications_ready", "applications-agent"),
}

# Required outcomes per profile. The default profile needs every domain; a
# narrower profile or a ``workflows`` context override can reduce this.
REQUIRED_GOALS_BY_PROFILE: dict[str, list[str]] = {
    "default": ["device", "access", "systems", "applications"],
    "contractor": ["device", "access"],
}

_INSTRUCTIONS = """You are the Onboarding coordinator. You own the outcome
`employee_onboarding_ready` for new employees.

You delegate business OUTCOMES to domain agents. You never perform domain
actions yourself and you never call domain tools or MockWorld. The only way
work gets done is by delegating one of these outcomes to its domain agent:

- employee_device_ready -> device-agent
- employee_access_ready -> access-agent
- employee_systems_ready -> systems-agent
- employee_applications_ready -> applications-agent

Your responsibilities:
1. Create an onboarding case and decide which outcomes are required.
2. Delegate every required outcome to its domain agent.
3. Track progress via case status and the case event timeline (poll with
   asyncio.sleep; never busy-loop).
4. Reconcile blockers: open a human task for ops-lead and wait for the
   decision, then re-delegate on approval; escalate stalled work by failing
   the workflow on a HITL timeout.
5. Verify readiness: READY only when every required outcome is COMPLETED with
   verified=true; otherwise NOT_READY, listing the missing goals.
"""


def _coerce_task_type(type_value: str | HumanTaskType) -> HumanTaskType:
    """Map a task-type string to a :class:`HumanTaskType`, defaulting safely."""
    if isinstance(type_value, HumanTaskType):
        return type_value
    try:
        return HumanTaskType(type_value)
    except ValueError:
        return HumanTaskType.EXCEPTION_HANDLING


class CoordinatorAgent:
    """Onboarding coordinator (SPEC §11): delegates outcomes, verifies readiness."""

    def __init__(
        self,
        backend_url: str = DEFAULT_BACKEND_URL,
        agent_id: str = DEFAULT_AGENT_ID,
        model: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.backend_url = backend_url.rstrip("/")
        self.agent_id = agent_id
        self.goal = DEFAULT_GOAL
        self.model = model or os.environ.get("AGENTLAB_MODEL") or "gemini-2.5-flash"
        self._transport = transport
        self._timeout = httpx.Timeout(10.0)

        # Delegation registry: workflow_id -> delegatee/owner metadata.
        self._delegations: dict[str, dict[str, Any]] = {}
        self._required_goals_by_case: dict[str, list[str]] = {}
        self._employee_by_case: dict[str, str] = {}

        self.tools = [
            self.create_case,
            self.delegate_workflow,
            self.get_case_status,
            self.list_case_events,
            self.request_human_intervention,
            self.get_task_status,
            self.escalate,
        ]
        self.team = TeamAgent(
            id=self.agent_id,
            goal=DEFAULT_GOAL,
            instructions=_INSTRUCTIONS,
            knowledge=MarkdownKnowledgeProvider(self._knowledge_dir()),
            tools=self.tools,
            model=self.model,
        )

    @property
    def agent(self) -> Any:
        """The underlying ADK agent, for callback attachment and inspection."""
        return self.team.agent

    @staticmethod
    def _knowledge_dir() -> str:
        return os.environ.get(
            "AGENTLAB_ONBOARDING_KNOWLEDGE",
            str(Path(__file__).resolve().parents[5] / "knowledge" / "onboarding"),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform one backend call, returning a flat dict or an error envelope."""
        headers = {"X-Agent-Id": agent_id or self.agent_id}
        try:
            async with httpx.AsyncClient(
                base_url=self.backend_url,
                headers=headers,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            return {"error": {"code": "NETWORK_ERROR", "description": str(exc)}}
        try:
            body = response.json()
        except ValueError:
            return {
                "error": {
                    "code": "BAD_RESPONSE",
                    "description": response.text.strip() or f"HTTP {response.status_code}",
                }
            }
        if isinstance(body, dict):
            return body
        return {
            "error": {
                "code": "BAD_RESPONSE",
                "description": f"Unexpected JSON from {method} {path}",
            }
        }

    @staticmethod
    def _has_error(body: dict[str, Any]) -> bool:
        return isinstance(body.get("error"), dict)

    # ---------------------------------------------------------------- tools --

    async def create_case(self, employee_id: str, context: dict[str, Any]) -> dict[str, Any]:
        """Create an onboarding case for ``employee_id`` and return its summary.

        Generates a fresh case id and posts it to the backend. ``context``
        carries the employee profile and any required-workflow override.
        """
        case_id = f"ONB-{uuid4().hex[:12]}"
        return await self._request(
            "POST",
            "/cases",
            json={"case_id": case_id, "employee_id": employee_id, "context": context},
        )

    async def delegate_workflow(
        self,
        case_id: str,
        goal: str,
        target_agent_id: str,
        employee_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Delegate one business outcome to a domain agent.

        This is the ONLY way work gets done: the coordinator never calls domain
        tools or MockWorld itself. Creates a fresh ``workflow_id`` and posts a
        workflow request targeting ``target_agent_id``.
        """
        workflow_id = str(uuid4())
        body = await self._request(
            "POST",
            "/workflows",
            json={
                "workflow_id": workflow_id,
                "case_id": case_id,
                "goal": goal,
                "employee_id": employee_id,
                "context": context,
                "target_agent_id": target_agent_id,
            },
        )
        if not self._has_error(body):
            self._delegations[workflow_id] = {
                "case_id": case_id,
                "goal": goal,
                "employee_id": employee_id,
                "agent_id": target_agent_id,
            }
        return body

    async def get_case_status(self, case_id: str) -> dict[str, Any]:
        """Return the case detail, including per-domain workflow status aggregation."""
        return await self._request("GET", f"/cases/{case_id}")

    async def list_case_events(self, case_id: str) -> dict[str, Any]:
        """Return the append-only event timeline for a case."""
        return await self._request("GET", f"/cases/{case_id}/events")

    async def request_human_intervention(
        self,
        case_id: str,
        workflow_id: str | None,
        type: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Open a human task for ops-lead to resolve a blocker or escalation.

        The coordinator asks a human, never improvises. ``context`` explains
        what needs a decision; the allowed actions are approve and reject.
        """
        task = HumanTask(
            human_task_id=f"HT-{uuid4().hex[:12]}",
            case_id=case_id,
            workflow_id=workflow_id or "",
            requested_by=self.agent_id,
            requested_from=OPS_LEAD,
            type=_coerce_task_type(type),
            context=dict(context),
            allowed_actions=["approve", "reject"],
            status="open",
            created_at=datetime.now(UTC),
        )
        return await self._request("POST", "/tasks", json=task.model_dump(mode="json"))

    async def get_task_status(self, human_task_id: str) -> dict[str, Any]:
        """Return the current state of a human task (open or resolved)."""
        return await self._request("GET", f"/tasks/{human_task_id}")

    async def escalate(self, workflow_id: str, reason: str) -> dict[str, Any]:
        """Fail a stalled delegated workflow with ``reason`` (escalation).

        Used when a human does not answer within DEC-08's no-response SLA. The
        failing is acted on behalf of the owning domain agent, which is stuck.
        """
        owner = self._delegations.get(workflow_id, {}).get("agent_id") or self.agent_id
        return await self._request(
            "POST",
            f"/workflows/{workflow_id}/fail",
            json={"reason": reason},
            agent_id=owner,
        )

    # ------------------------------------------------------------ reasoning --

    def _resolve_domains(
        self,
        workflows: list[str] | None,
        context: dict[str, Any],
    ) -> list[str]:
        """Determine the required goal domains for a case."""
        if workflows:
            return [domain for domain in workflows if domain in DOMAIN_GOALS]
        override = context.get("workflows")
        if isinstance(override, (list, tuple, set, frozenset)) and override:
            return [d for d in override if isinstance(d, str) and d in DOMAIN_GOALS]
        profile = context.get("profile")
        if isinstance(profile, str) and profile in REQUIRED_GOALS_BY_PROFILE:
            return list(REQUIRED_GOALS_BY_PROFILE[profile])
        role = context.get("role")
        if isinstance(role, str) and role.casefold() in {"contractor", "vendor"}:
            return list(REQUIRED_GOALS_BY_PROFILE["contractor"])
        return list(REQUIRED_GOALS_BY_PROFILE["default"])

    @staticmethod
    def _reduce_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Reconstruct each workflow's current state from the event timeline."""
        states: dict[str, dict[str, Any]] = {}
        for event in events:
            workflow_id = event.get("workflow_id")
            if not isinstance(workflow_id, str):
                continue
            state = states.setdefault(
                workflow_id,
                {"status": "acknowledged", "verified": False, "blockers": [], "reason": None},
            )
            event_type = event.get("type")
            payload: Any = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            if event_type == "WORKFLOW_ACKNOWLEDGED":
                state["status"] = "running"
            elif event_type == "WORKFLOW_STATUS":
                if isinstance(payload.get("status"), str):
                    state["status"] = payload["status"]
                if isinstance(payload.get("blockers"), list):
                    state["blockers"] = payload["blockers"]
            elif event_type == "BLOCKER_CREATED":
                state["status"] = "blocked"
                state["blockers"].append(payload)
            elif event_type == "OUTCOME_VERIFIED":
                state["status"] = "completed"
                state["verified"] = True
            elif event_type == "WORKFLOW_FAILED":
                state["status"] = "running" if payload.get("retry") else "failed"
                state["reason"] = payload.get("reason")
        return states

    @staticmethod
    def _goals_from_events(events: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Map each delegated goal to its (ordered) workflow ids."""
        goals: dict[str, list[str]] = {}
        for event in events:
            if event.get("type") != "WORKFLOW_DELEGATED":
                continue
            workflow_id = event.get("workflow_id")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            goal = payload.get("goal")
            if isinstance(workflow_id, str) and isinstance(goal, str):
                goals.setdefault(goal, []).append(workflow_id)
        return goals

    async def _snapshot(
        self,
        case_id: str,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
        """Return a fresh (:func:`_reduce_events`, :func:`_goals_from_events`) pair."""
        events = await self.list_case_events(case_id)
        if isinstance(events.get("events"), list):
            event_list: list[dict[str, Any]] = events["events"]
        else:
            event_list = []
        return self._reduce_events(event_list), self._goals_from_events(event_list)

    async def _await_decision(self, human_task_id: str) -> dict[str, Any] | None:
        """Poll a human task until resolved, returning ``None`` on SLA timeout."""
        deadline = time.monotonic() + HITL_NO_RESPONSE_SECONDS
        while True:
            task = await self.get_task_status(human_task_id)
            if self._has_error(task):
                return {"decision": "reject"}
            if task.get("status") == "resolved":
                decision = task.get("decision")
                return decision if isinstance(decision, dict) else {}
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _reconcile_blocker(
        self,
        case_id: str,
        workflow_id: str,
        domain: str,
        employee_id: str,
        context: dict[str, Any],
        state: dict[str, Any],
    ) -> str | None:
        """Request human intervention for a blocked workflow.

        Returns the replacement workflow id on an approved re-delegation, else
        ``None`` (rejection, escalation, or timeout marks the goal not-ready).
        """
        goal, target = DOMAIN_GOALS[domain]
        blockers = state.get("blockers") if isinstance(state.get("blockers"), list) else []
        task = await self.request_human_intervention(
            case_id,
            workflow_id,
            "APPROVAL",
            {"reason": "workflow_blocked", "goal": goal, "blockers": blockers},
        )
        if self._has_error(task):
            await self.escalate(workflow_id, "unable_to_open_human_task")
            return None
        human_task_id = task.get("human_task_id")
        decision = (
            await self._await_decision(str(human_task_id))
            if isinstance(human_task_id, str)
            else None
        )
        if decision is None:
            await self.escalate(workflow_id, "hitl_timeout")
            return None
        if decision.get("decision") != "approve":
            return None
        summary = await self.delegate_workflow(case_id, goal, target, employee_id, context)
        if self._has_error(summary) or not isinstance(summary.get("workflow_id"), str):
            return None
        return str(summary["workflow_id"])

    async def _await_outcomes(
        self,
        case_id: str,
        domains: list[str],
        current_wf: dict[str, str],
        employee_id: str,
        context: dict[str, Any],
    ) -> None:
        """Poll each delegated workflow until terminal, reconciling blockers."""
        pending = set(domains)
        reconciling: set[str] = set()
        while pending:
            states, _goal_wfs = await self._snapshot(case_id)
            for domain in list(pending):
                workflow_id = current_wf.get(domain)
                if workflow_id is None:
                    pending.discard(domain)
                    continue
                state = states.get(
                    workflow_id,
                    {"status": "acknowledged", "verified": False, "blockers": [], "reason": None},
                )
                status = state["status"]
                if status in {"completed", "failed"}:
                    pending.discard(domain)
                    continue
                if status == "blocked" and domain not in reconciling:
                    reconciling.add(domain)
                    replacement = await self._reconcile_blocker(
                        case_id,
                        workflow_id,
                        domain,
                        employee_id,
                        context,
                        state,
                    )
                    if replacement is not None:
                        current_wf[domain] = replacement
                        reconciling.discard(domain)
                    else:
                        pending.discard(domain)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def _failed_verdict(
        self,
        employee_id: str | None,
        case_id: str | None,
        goal: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        error: Any = body.get("error")
        if not isinstance(error, dict):
            error = {}
        return {
            "employee_id": employee_id,
            "case_id": case_id,
            "verdict": "NOT_READY",
            "ready_goals": [],
            "missing_goals": [
                {
                    "goal": goal,
                    "status": "failed",
                    "blockers": [
                        {
                            "code": error.get("code", "ERROR"),
                            "description": error.get("description", "case setup failed"),
                        }
                    ],
                }
            ],
        }

    async def run_onboarding(
        self,
        employee_id: str,
        case_id: str | None = None,
        workflows: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run the full scripted onboarding flow and return the readiness verdict.

        Creates the case (or reuses ``case_id``), determines the required goals
        from ``workflows`` or the profile map, delegates each, polls until every
        workflow is terminal (reconciling blockers through the HITL loop), then
        computes and returns the verdict.
        """
        if case_id is None:
            context: dict[str, Any] = (
                {"workflows": list(workflows)} if workflows is not None else {}
            )
            created = await self.create_case(employee_id, context)
            if self._has_error(created):
                return self._failed_verdict(employee_id, None, "case", created)
            case_id = str(created["case_id"])
            case_context = context
        else:
            existing = await self.get_case_status(case_id)
            if self._has_error(existing):
                return self._failed_verdict(employee_id, case_id, "case", existing)
            case_context = dict(existing.get("context") or {})
            if workflows is not None:
                case_context["workflows"] = list(workflows)

        domains = self._resolve_domains(workflows, case_context)
        self._required_goals_by_case[case_id] = domains
        self._employee_by_case[case_id] = employee_id

        current_wf: dict[str, str] = {}
        for domain in domains:
            goal, target = DOMAIN_GOALS[domain]
            summary = await self.delegate_workflow(
                case_id, goal, target, employee_id, case_context
            )
            if not self._has_error(summary) and isinstance(summary.get("workflow_id"), str):
                current_wf[domain] = str(summary["workflow_id"])

        await self._await_outcomes(case_id, domains, current_wf, employee_id, case_context)
        return await self.run_verdict(case_id)

    async def run_verdict(self, case_id: str) -> dict[str, Any]:
        """Aggregate required-goal outcomes and blockers into a verdict.

        READY iff every required goal has a workflow COMPLETED with
        verified=true; otherwise NOT_READY, listing the missing goals.
        """
        case = await self.get_case_status(case_id)
        if self._has_error(case):
            return self._failed_verdict(None, case_id, "case", case)
        employee_id = case.get("employee_id")

        _states, goal_workflows = await self._snapshot(case_id)

        domains = self._required_goals_by_case.get(case_id)
        if domains:
            required_goals = [
                DOMAIN_GOALS[domain][0] for domain in domains if domain in DOMAIN_GOALS
            ]
        else:
            required_goals = list(goal_workflows.keys())

        ready: list[str] = []
        missing: list[dict[str, Any]] = []
        for goal in required_goals:
            workflow_ids = goal_workflows.get(goal, [])
            completed = any(
                _states.get(wf, {}).get("status") == "completed"
                and bool(_states.get(wf, {}).get("verified"))
                for wf in workflow_ids
            )
            if completed:
                ready.append(goal)
                continue
            workflow_id = workflow_ids[-1] if workflow_ids else None
            state = _states.get(workflow_id, {}) if workflow_id else {}
            missing.append(
                {
                    "goal": goal,
                    "status": state.get("status", "not_delegated"),
                    "blockers": list(state.get("blockers", [])),
                }
            )

        return {
            "employee_id": employee_id,
            "case_id": case_id,
            "verdict": "READY" if not missing else "NOT_READY",
            "ready_goals": ready,
            "missing_goals": missing,
        }


__all__ = [
    "DEFAULT_GOAL",
    "DOMAIN_GOALS",
    "POLL_INTERVAL_SECONDS",
    "REQUIRED_GOALS_BY_PROFILE",
    "CoordinatorAgent",
]
