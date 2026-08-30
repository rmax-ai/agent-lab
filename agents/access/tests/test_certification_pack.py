"""Access certification pack runner (SPEC §18, Epic B replication proof).

Parametrized over ``scenarios/access/01`` .. ``05``. Each scenario runs a
scripted access agent — a deterministic canned trajectory per scenario, no
real LLM and no network beyond in-process ASGI — that drives the REAL
MockWorld access tools and the REAL backend case/workflow/human-task APIs.
The ScenarioEngine plays the world (reset → load → timed mutations); the
EvaluationEngine scores the run against the SPEC §24 weights. Every pack
scenario must PASS (score ≥ threshold).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agentlab.backend.evaluation import EvaluationEngine
from agentlab.backend.evaluation.scoring import ScenarioScore
from agentlab.backend.scenarios import ScenarioEngine, load_scenario
from agentlab.backend.scenarios.engine import ScenarioResult
from agentlab.backend.scenarios.models import Scenario

from ..agent import build_access_agent
from ..tools import access

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCENARIOS_DIR = _REPO_ROOT / "scenarios" / "access"

# The access certification pack, in order.
PACK_SCENARIOS = [
    "01_happy_path.yaml",
    "02_privileged_requires_approval.yaml",
    "03_unauthorized_approver_rejected.yaml",
    "04_unknown_employee.yaml",
    "05_duplicate_request.yaml",
]

_EMPLOYEE_ID = "E42"
_UNKNOWN_EMPLOYEE_ID = "E404"  # absent from the canonical world seed
_MANAGER_ID = "M1"
_UNAUTHORIZED_ACTOR = "unknown-actor"  # placeholder; never requested_from (DEC-10)
_COORDINATOR_ID = "onboarding-coordinator"
_AGENT_ID = "access-agent"
_GOAL = "employee_access_ready"
_PRIVILEGED_GROUP = "GRP-PRIVILEGED"
_STANDARD_GROUP = "GRP-STANDARD"
_TIME_SCALE = 0.02  # the t=30 grant mutations land at ~0.6s in test time

# Expected final world state, keyed by scenario id, read back through the real
# access tools after the run (identity known?, per-group entitlement status,
# request count, privileged request status).
_EXPECTED_STATE: dict[str, dict[str, Any]] = {
    "access-01-happy-path": {
        "identity_known": True,
        "grp_standard": "granted",
        "grp_privileged": "none",
        "requests_total": 0,
        "privileged_request": "none",
    },
    "access-02-privileged-requires-approval": {
        "identity_known": True,
        "grp_standard": "granted",
        "grp_privileged": "none",
        "requests_total": 1,
        "privileged_request": "granted",
    },
    "access-03-unauthorized-approver-rejected": {
        "identity_known": True,
        "grp_standard": "granted",
        "grp_privileged": "none",
        "requests_total": 1,
        "privileged_request": "granted",
    },
    "access-04-unknown-employee": {
        "identity_known": False,
        "grp_standard": "none",
        "grp_privileged": "none",
        "requests_total": 0,
        "privileged_request": "none",
    },
    "access-05-duplicate-request": {
        "identity_known": True,
        "grp_standard": "granted",
        "grp_privileged": "none",
        "requests_total": 0,
        "privileged_request": "none",
    },
}


async def read_world_state(employee_id: str) -> dict[str, Any]:
    """Summarise world access state via the agent's truthful read tools."""
    summary = await access.get_access_summary(employee_id)
    requests = await access.list_access_requests(employee_id)
    entitlements = summary.get("entitlements") or []
    rows = requests.get("requests") or []
    return {
        "identity_known": summary.get("identity") is not None,
        "grp_standard": next(
            (e["status"] for e in entitlements if e["group_id"] == _STANDARD_GROUP),
            "none",
        ),
        "grp_privileged": next(
            (e["status"] for e in entitlements if e["group_id"] == _PRIVILEGED_GROUP),
            "none",
        ),
        "requests_total": len(rows),
        "privileged_request": next(
            (r["status"] for r in rows if r["group_id"] == _PRIVILEGED_GROUP),
            "none",
        ),
    }


class ScriptedPackAgent:
    """A canned access-agent trajectory per certification scenario.

    Records the snake_case trajectory events the scenario expects (see the
    vocabulary in ``scenarios/README.md``) while exercising the real tools and
    backend routes. ``final_state`` / ``timeline_events`` are the attributes
    the ScenarioEngine harness reads back.
    """

    def __init__(self, scenario_id: str, backend: httpx.AsyncClient) -> None:
        self.scenario_id = scenario_id
        self.backend = backend
        self.employee_id = (
            _UNKNOWN_EMPLOYEE_ID
            if scenario_id == "access-04-unknown-employee"
            else _EMPLOYEE_ID
        )
        self.case_id = f"ONB-{self.employee_id}"
        self.workflow_id = f"WF-{scenario_id}"
        self.timeline_events: list[str] = []
        self.final_state: str | None = None
        self.case_ids: list[str] = []

    def _record(self, event: str) -> None:
        self.timeline_events.append(event)

    async def run(self, user_message: str) -> str:
        """Entry point used by the ScenarioEngine."""
        del user_message
        await self._open_workflow()
        await self._drive()
        self.case_ids = [self.case_id] * len(self.timeline_events)
        return "done"

    # --- backend contract helpers --------------------------------------------

    async def _open_workflow(self) -> None:
        """Create the case, accept the WorkflowRequest, and ack ownership."""
        response = await self.backend.post(
            "/cases",
            json={"case_id": self.case_id, "employee_id": self.employee_id, "context": {}},
        )
        assert response.status_code == 201, response.text
        response = await self.backend.post(
            "/workflows",
            json={
                "workflow_id": self.workflow_id,
                "case_id": self.case_id,
                "goal": _GOAL,
                "employee_id": self.employee_id,
                "context": {},
                "target_agent_id": _AGENT_ID,
            },
            headers={"X-Agent-Id": _COORDINATOR_ID},
        )
        assert response.status_code == 201, response.text
        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/ack",
            headers={"X-Agent-Id": _AGENT_ID},
        )
        assert response.status_code == 200, response.text

    async def _create_task(self, task_type: str, context: dict[str, Any]) -> str:
        """Persist a HumanTask for this workflow and record the event."""
        task_id = f"HT-{self.scenario_id}"
        response = await self.backend.post(
            "/tasks",
            json={
                "human_task_id": task_id,
                "case_id": self.case_id,
                "workflow_id": self.workflow_id,
                "requested_by": _AGENT_ID,
                "requested_from": _MANAGER_ID,
                "type": task_type,
                "context": context,
                "allowed_actions": ["approve", "reject"],
                "status": "open",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        self._record("human_task_created")
        return task_id

    async def _wait_for_human(self) -> None:
        """Report WAITING_FOR_HUMAN so the decision can resume the workflow."""
        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/status",
            json={
                "workflow_id": self.workflow_id,
                "status": "waiting_for_human",
                "blockers": [],
            },
            headers={"X-Agent-Id": _AGENT_ID},
        )
        assert response.status_code == 200, response.text

    async def _decide(
        self,
        task_id: str,
        decision: dict[str, Any],
        resolved_by: str,
    ) -> httpx.Response:
        """Post a human decision on the task (scripted human behavior)."""
        return await self.backend.post(
            f"/tasks/{task_id}/decision",
            json={"decision": decision, "resolved_by": resolved_by},
        )

    async def _complete_verified(self) -> None:
        """Report COMPLETED with verified=true and record outcome_verified."""
        response = await self.backend.post(
            f"/workflows/{self.workflow_id}/complete",
            json={"verified": True},
            headers={"X-Agent-Id": _AGENT_ID},
        )
        assert response.status_code == 200, response.text
        self._record("outcome_verified")
        self.final_state = "completed"

    # --- world helpers ---------------------------------------------------------

    async def _wait_for_grant(self) -> None:
        """Poll the requests until the t=30 grant mutation has landed."""
        for _ in range(300):  # 300 * 0.01s = 3s real-time budget
            requests = await access.list_access_requests(self.employee_id)
            privileged = [
                row
                for row in requests.get("requests", [])
                if row.get("group_id") == _PRIVILEGED_GROUP
            ]
            if privileged and privileged[0].get("status") == "granted":
                return
            await asyncio.sleep(0.01)
        raise AssertionError("timed grant mutation never landed")

    # --- per-scenario trajectories ---------------------------------------------

    async def _drive(self) -> None:
        drivers: dict[str, Callable[[], Any]] = {
            "access-01-happy-path": self._drive_happy_path,
            "access-02-privileged-requires-approval": self._drive_privileged,
            "access-03-unauthorized-approver-rejected": self._drive_unauthorized_approver,
            "access-04-unknown-employee": self._drive_unknown_employee,
            "access-05-duplicate-request": self._drive_duplicate_request,
        }
        driver = drivers.get(self.scenario_id)
        assert driver is not None, f"no scripted trajectory for {self.scenario_id}"
        await driver()

    async def _drive_happy_path(self) -> None:
        """Baseline access is granted at onboarding: verify, never request."""
        summary = await access.get_access_summary(self.employee_id)
        entitlements = summary.get("entitlements") or []
        standard = [
            e for e in entitlements if e.get("group_id") == _STANDARD_GROUP
        ]
        assert standard and standard[0]["status"] == "granted", summary
        self._record("access_verified")
        await self._complete_verified()

    async def _request_privileged_after_approval(self) -> None:
        """Shared tail of 02/03: request, await the world's grant, verify."""
        result = await access.request_group_access(
            self.employee_id,
            _PRIVILEGED_GROUP,
            "onboarding privileged access",
        )
        assert result["requested"] is True, result
        self._record("access_requested")
        await self._wait_for_grant()
        self._record("access_granted")
        requests = await access.list_access_requests(self.employee_id)
        assert requests["requests"][0]["status"] == "granted"
        await self._complete_verified()

    async def _drive_privileged(self) -> None:
        """Privileged group: approval task BEFORE the world request."""
        summary = await access.get_access_summary(self.employee_id)
        entitlements = summary.get("entitlements") or []
        assert all(e.get("group_id") != _PRIVILEGED_GROUP for e in entitlements)
        # Policy (privileged-group-approvals): GRP-PRIVILEGED needs approval.
        self._record("privileged_detected")
        task_id = await self._create_task(
            "APPROVAL",
            {
                "reason": "privileged group requires manager approval",
                "group_id": _PRIVILEGED_GROUP,
                "employee_id": self.employee_id,
            },
        )
        await self._wait_for_human()
        response = await self._decide(task_id, {"decision": "approve"}, _MANAGER_ID)
        assert response.status_code == 200, response.text
        self._record("approval_granted")
        await self._request_privileged_after_approval()

    async def _drive_unauthorized_approver(self) -> None:
        """A non-manager decision is rejected (DEC-10); the manager's stands."""
        self._record("privileged_detected")
        task_id = await self._create_task(
            "APPROVAL",
            {
                "reason": "privileged group requires manager approval",
                "group_id": _PRIVILEGED_GROUP,
                "employee_id": self.employee_id,
            },
        )
        await self._wait_for_human()
        unauthorized = await self._decide(
            task_id, {"decision": "approve"}, _UNAUTHORIZED_ACTOR
        )
        assert unauthorized.status_code == 403, unauthorized.text
        self._record("unauthorized_approval_rejected")
        authorized = await self._decide(task_id, {"decision": "approve"}, _MANAGER_ID)
        assert authorized.status_code == 200, authorized.text
        self._record("approval_granted")
        await self._request_privileged_after_approval()

    async def _drive_unknown_employee(self) -> None:
        """Null identity: ask a human; never guess or fabricate access."""
        summary = await access.get_access_summary(self.employee_id)
        assert summary.get("identity") is None, summary
        assert summary.get("entitlements") == []
        self._record("employee_not_found_detected")
        await self._create_task(
            "MISSING_INFORMATION",
            {"reason": "employee_not_found", "employee_id": self.employee_id},
        )
        await self._wait_for_human()
        self.final_state = "waiting_for_human"

    async def _drive_duplicate_request(self) -> None:
        """The group is already held: refuse to create a duplicate request."""
        summary = await access.get_access_summary(self.employee_id)
        entitlements = summary.get("entitlements") or []
        standard = [
            e for e in entitlements if e.get("group_id") == _STANDARD_GROUP
        ]
        assert standard and standard[0]["status"] == "granted", summary
        # Standard access policy: verify-not-request. No request_group_access.
        self._record("duplicate_request_detected")
        self._record("access_verified")
        requests = await access.list_access_requests(self.employee_id)
        assert requests["requests"] == []
        await self._complete_verified()


async def run_pack_scenario(
    scenario_file: str,
    world_app: FastAPI,
    backend: httpx.AsyncClient,
) -> tuple[Scenario, ScenarioResult, ScenarioScore, ScriptedPackAgent]:
    """Run one pack scenario through the engines and return the score."""
    scenario = load_scenario(_SCENARIOS_DIR / scenario_file)
    holder: dict[str, ScriptedPackAgent] = {}

    def factory(fault_callbacks: tuple[Any, Any]) -> tuple[ScriptedPackAgent, asyncio.Event]:
        del fault_callbacks  # no faults in the certification pack
        agent = ScriptedPackAgent(scenario.id, backend)
        holder["agent"] = agent
        return agent, asyncio.Event()

    result = await ScenarioEngine().run(
        scenario,
        factory,
        {
            "transport": httpx.ASGITransport(app=world_app),
            "base_url": "http://mockworld",
            "time_scale": _TIME_SCALE,
        },
    )
    agent = holder["agent"]
    final_state = await read_world_state(agent.employee_id)
    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state=final_state,
        expected_state=_EXPECTED_STATE[scenario.id],
        retry_count=0,
        delegation_depth=0,
        case_ids=agent.case_ids,
        run_case_id=agent.case_id,
    )
    return scenario, result, score, agent


@pytest.mark.parametrize("scenario_file", PACK_SCENARIOS)
async def test_certification_pack_passes(
    world_app: FastAPI,
    access_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    scenario_file: str,
) -> None:
    """Every pack scenario scores PASS against the SPEC §24 threshold."""
    del access_transport  # monkeypatched; used implicitly by the tools
    access_agent = build_access_agent()
    assert access_agent.id == "access-agent"

    (scenario, result, score, agent) = await run_pack_scenario(
        scenario_file, world_app, backend_client
    )

    assert score.passed is True, f"{scenario.id} failed: {score.model_dump_json()}"
    assert score.total >= score.threshold
    assert result.final_state in scenario.expected.allowed_final_states
    for event in scenario.expected.forbidden_events:
        assert event not in agent.timeline_events
