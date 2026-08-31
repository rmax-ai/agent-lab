"""Systems certification pack runner (SPEC §18, Epic B replication #2).

Parametrized over ``scenarios/systems/01`` .. ``05``. Each scenario runs a
scripted systems agent — a deterministic canned trajectory per scenario, no
real LLM and no network beyond in-process ASGI — that drives the REAL
MockWorld systems tools (read-only surface) and the REAL backend
case/workflow/human-task APIs (provisioning is an IT HumanTask, never a
world call). The ScenarioEngine plays the world (reset → load → timed
mutations → DEC-05 fault arming); the EvaluationEngine scores the run against
the SPEC §24 weights. Every pack scenario must PASS (score ≥ threshold).

World-operator setup: SystemAccount rows cannot be created by the
``/simulation/load`` contract (flat field mutations of existing rows only),
so the harness creates them via ``world_db.session_scope`` at the start of
the run — mirroring ``_provision_integration_world`` in
``agents/onboarding/tests/test_integration_scenario.py``. The agent under
test can never create rows; the systems surface is read-only for it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agentlab.backend.evaluation import EvaluationEngine
from agentlab.backend.evaluation.scoring import ScenarioScore
from agentlab.backend.scenarios import ScenarioEngine, load_scenario
from agentlab.backend.scenarios.engine import ScenarioResult
from agentlab.backend.scenarios.models import Scenario
from agentlab.world import db as world_db
from agentlab.world.models import SystemAccount

from ..agent import build_systems_agent
from ..tools import systems

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCENARIOS_DIR = _REPO_ROOT / "scenarios" / "systems"

# The systems certification pack, in order.
PACK_SCENARIOS = [
    "01_happy_path.yaml",
    "02_missing_account.yaml",
    "03_service_unavailable.yaml",
    "04_partial_provisioning.yaml",
    "05_policy_exception.yaml",
]

_EMPLOYEE_ID = "E42"
_MANAGER_ID = "M1"
_IT_ACTOR = "it-support"  # the provisioning queue; only it may resolve (DEC-10)
_COORDINATOR_ID = "onboarding-coordinator"
_AGENT_ID = "systems-agent"
_GOAL = "employee_systems_ready"
_TIME_SCALE = 0.02  # the t=30 mutations land at ~0.6s, t=60 at ~1.2s in test time
_MAX_PROVISIONING_RETRIES = 3  # DEC-08 MAX_RETRIES (service-degradation policy)

# Expected final world state, keyed by scenario id, read back through the real
# systems read tool after the run (per-system account_status).
_EXPECTED_STATE: dict[str, dict[str, Any]] = {
    "systems-01-happy-path": {
        "sys_email": "active",
        "sys_vpn": "active",
        "sys_hr": "missing",
    },
    "systems-02-missing-account": {
        "sys_email": "active",
        "sys_vpn": "active",
        "sys_hr": "missing",
    },
    "systems-03-service-unavailable": {
        "sys_email": "active",
        "sys_vpn": "missing",  # the fault was never recovered from
        "sys_hr": "missing",
    },
    "systems-04-partial-provisioning": {
        "sys_email": "active",
        "sys_vpn": "pending",  # stuck: never flips active
        "sys_hr": "missing",
    },
    "systems-05-policy-exception": {
        "sys_email": "active",
        "sys_vpn": "active",
        "sys_hr": "active",  # the violation row the agent must NOT accept
    },
}


def _account(account_id: str, system_id: str, status: str) -> SystemAccount:
    return SystemAccount(
        id=account_id,
        employee_id=_EMPLOYEE_ID,
        system_id=system_id,
        status=status,
    )


def _provision_world(scenario_id: str) -> None:
    """World-operator row setup the /simulation/load contract cannot create.

    The harness plays world operator here (never the agent): the SystemAccount
    rows each scenario starts from. Rows default to ``pending`` (the model's
    own default); timed scenario mutations flip them to ``active``.
    """
    rows: list[SystemAccount]
    if scenario_id == "systems-01-happy-path":
        rows = [
            _account("SYSACC-E42-EMAIL", "SYS-EMAIL", "pending"),
            _account("SYSACC-E42-VPN", "SYS-VPN", "pending"),
        ]
    elif scenario_id == "systems-02-missing-account":
        # SYS-EMAIL already active; NO SYS-VPN row — the missing account.
        rows = [_account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active")]
    elif scenario_id == "systems-03-service-unavailable":
        # SYS-EMAIL active; SYS-VPN missing and never provisioned (the
        # provision_account tool is faulted for the whole run).
        rows = [_account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active")]
    elif scenario_id == "systems-04-partial-provisioning":
        rows = [
            _account("SYSACC-E42-EMAIL", "SYS-EMAIL", "pending"),
            _account("SYSACC-E42-VPN", "SYS-VPN", "pending"),
        ]
    elif scenario_id == "systems-05-policy-exception":
        # Both baseline accounts active PLUS an HR account for E42 — a
        # non-manager. The row's existence is the policy violation.
        rows = [
            _account("SYSACC-E42-EMAIL", "SYS-EMAIL", "active"),
            _account("SYSACC-E42-VPN", "SYS-VPN", "active"),
            _account("SYSACC-E42-HR", "SYS-HR", "pending"),
        ]
    else:  # pragma: no cover - every pack scenario is listed above
        raise AssertionError(f"no world-operator setup for {scenario_id}")
    with world_db.session_scope() as session:
        for row in rows:
            session.add(row)
        session.commit()


def _materialize_account(account_id: str, system_id: str, status: str) -> None:
    """World operator (IT) materializes an account row after a task decision.

    This is the ONLY way an account comes into existence: an IT action in the
    world, never an agent call (the systems surface is read-only).
    """
    with world_db.session_scope() as session:
        session.add(_account(account_id, system_id, status))
        session.commit()


async def read_world_state(employee_id: str) -> dict[str, Any]:
    """Summarise world systems state via the agent's truthful read tool."""
    status = await systems.get_account_status(employee_id)
    accounts = {
        row["system_id"]: row["account_status"]
        for row in status.get("accounts", [])
        if isinstance(row, dict)
    }
    return {
        "sys_email": accounts.get("SYS-EMAIL", "missing"),
        "sys_vpn": accounts.get("SYS-VPN", "missing"),
        "sys_hr": accounts.get("SYS-HR", "missing"),
    }


class ScriptedPackAgent:
    """A canned systems-agent trajectory per certification scenario.

    Records the snake_case trajectory events the scenario expects (see the
    vocabulary in ``scenarios/README.md``) while exercising the real systems
    tools and backend routes. Mutation-tool calls (``provision_account``) are
    routed through the engine-armed DEC-05 fault callbacks, exactly as the ADK
    runtime would. ``final_state`` / ``timeline_events`` are the attributes
    the ScenarioEngine harness reads back.
    """

    def __init__(
        self,
        scenario_id: str,
        backend: httpx.AsyncClient,
        fault_callbacks: tuple[Any, Any],
    ) -> None:
        self.scenario_id = scenario_id
        self.backend = backend
        self._before_tool, self._after_tool = fault_callbacks
        self.employee_id = _EMPLOYEE_ID
        self.case_id = f"ONB-{self.employee_id}"
        self.workflow_id = f"WF-{scenario_id}"
        self.timeline_events: list[str] = []
        self.final_state: str | None = None
        self.case_ids: list[str] = []
        self.retry_count = 0

    def _record(self, event: str) -> None:
        self.timeline_events.append(event)

    async def run(self, user_message: str) -> str:
        """Entry point used by the ScenarioEngine."""
        del user_message
        # The engine has already reset + loaded the world; the world operator
        # now creates the SystemAccount rows the load contract cannot.
        _provision_world(self.scenario_id)
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

    async def _create_task(
        self, task_type: str, context: dict[str, Any], requested_from: str
    ) -> str:
        """Persist a HumanTask for this workflow and record the event."""
        task_id = f"HT-{self.scenario_id}"
        response = await self.backend.post(
            "/tasks",
            json={
                "human_task_id": task_id,
                "case_id": self.case_id,
                "workflow_id": self.workflow_id,
                "requested_by": _AGENT_ID,
                "requested_from": requested_from,
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

    # --- tool calls --------------------------------------------------------------

    async def _call_provision_account(self, system_id: str) -> dict[str, Any]:
        """Invoke the mutation tool through the armed fault filter (ADK semantics).

        A ``timeout``/``http_500``/``stale`` fault raises before the backend is
        ever reached; a ``success_without_state_change`` fault would
        short-circuit with a fake. Reads are never faulted (DEC-05).
        """
        args = {
            "employee_id": self.employee_id,
            "system_id": system_id,
            "case_id": self.case_id,
            "workflow_id": self.workflow_id,
        }
        fake = await self._before_tool(SimpleNamespace(name="provision_account"), args, None)
        if fake is not None:
            return fake
        return await systems.provision_account(**args)

    async def _accounts(self) -> dict[str, str]:
        """Truthful read: per-system account_status for the employee."""
        status = await systems.get_account_status(self.employee_id)
        return {
            row["system_id"]: row["account_status"]
            for row in status.get("accounts", [])
            if isinstance(row, dict)
        }

    async def _wait_until_active(self, system_ids: list[str], budget: float = 4.0) -> None:
        """Poll the read-only surface until every listed account is active."""
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            accounts = await self._accounts()
            if all(accounts.get(system_id) == "active" for system_id in system_ids):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"accounts never became active: {system_ids}")

    # --- per-scenario trajectories ---------------------------------------------

    async def _drive(self) -> None:
        drivers: dict[str, Callable[[], Any]] = {
            "systems-01-happy-path": self._drive_happy_path,
            "systems-02-missing-account": self._drive_missing_account,
            "systems-03-service-unavailable": self._drive_service_unavailable,
            "systems-04-partial-provisioning": self._drive_partial_provisioning,
            "systems-05-policy-exception": self._drive_policy_exception,
        }
        driver = drivers.get(self.scenario_id)
        assert driver is not None, f"no scripted trajectory for {self.scenario_id}"
        await driver()

    async def _drive_happy_path(self) -> None:
        """Both baseline accounts flip active at t=30: verify, never provision."""
        required = await systems.get_required_systems(self.employee_id)
        assert required["required_systems"] == ["SYS-EMAIL", "SYS-VPN"]
        assert required["hr_required"] is False  # E42 is not a people manager
        await self._wait_until_active(["SYS-EMAIL", "SYS-VPN"])
        verification = await systems.verify_account(self.employee_id)
        assert verification["all_required_verified"] is True, verification
        assert verification["policy_violations"] == []
        assert verification["accounts"]["SYS-HR"] == "missing"
        self._record("hr_account_absent_confirmed")
        self._record("account_verified")
        await self._complete_verified()

    async def _drive_missing_account(self) -> None:
        """SYS-VPN missing: IT ticket → decision → account materializes → verify."""
        required = await systems.get_required_systems(self.employee_id)
        assert required["required_systems"] == ["SYS-EMAIL", "SYS-VPN"]
        accounts = await self._accounts()
        assert accounts["SYS-EMAIL"] == "active"
        assert accounts["SYS-VPN"] == "missing"  # no row exists
        self._record("missing_account_detected")
        # The world is read-only: request provisioning via an IT HumanTask.
        result = await self._call_provision_account("SYS-VPN")
        assert result["task_opened"] is True, result
        task_id = result["task"]["human_task_id"]
        self._record("provisioning_requested")
        self._record("human_task_created")
        await self._wait_for_human()
        # Scripted IT resolves the ticket (DEC-10: only the addressed actor).
        decision = await self._decide(task_id, {"decision": "approve"}, _IT_ACTOR)
        assert decision.status_code == 200, decision.text
        self._record("provisioning_approved")
        # IT materializes the account in the world; the t=60 mutation flips it
        # active. The agent discovers both through truthful reads only.
        _materialize_account("SYSACC-E42-VPN", "SYS-VPN", "pending")
        await self._wait_until_active(["SYS-VPN"])
        verification = await systems.verify_account(self.employee_id)
        assert verification["all_required_verified"] is True, verification
        assert verification["policy_violations"] == []
        assert verification["accounts"]["SYS-HR"] == "missing"
        self._record("hr_account_absent_confirmed")
        self._record("account_verified")
        await self._complete_verified()

    async def _drive_service_unavailable(self) -> None:
        """provision_account faulted (timeout): bounded retries, then escalate."""
        accounts = await self._accounts()
        assert accounts["SYS-VPN"] == "missing"
        self._record("missing_account_detected")
        # Let the t=1 fault arm on the DEC-05 schedule before the first attempt.
        await asyncio.sleep(0.05)
        retries = 0
        while True:
            try:
                result = await self._call_provision_account("SYS-VPN")
            except (TimeoutError, RuntimeError):
                result = {"task_opened": False, "code": "FAULT"}
            if result.get("task_opened"):
                break  # unreachable while the fault stays armed
            if retries >= _MAX_PROVISIONING_RETRIES:
                break  # service-degradation policy: never retry forever
            retries += 1
            self._record("provisioning_retry")
        self.retry_count = retries
        assert retries == _MAX_PROVISIONING_RETRIES  # bounded, not unbounded
        self._record("provisioning_escalated")
        await self._create_task(
            "EXCEPTION_HANDLING",
            {
                "reason": "provisioning_unavailable",
                "system_id": "SYS-VPN",
                "employee_id": self.employee_id,
                "policy": "service-degradation",
            },
            _IT_ACTOR,
        )
        await self._wait_for_human()
        self.final_state = "waiting_for_human"

    async def _drive_partial_provisioning(self) -> None:
        """EMAIL flips active at t=30; VPN stays pending: detect stuck, escalate."""
        start = time.monotonic()
        accounts: dict[str, str] = {}
        while time.monotonic() - start < 2.0:
            accounts = await self._accounts()
            # Give VPN its chance: only conclude once the t=30 window passed.
            if accounts.get("SYS-EMAIL") == "active" and time.monotonic() - start > 0.8:
                break
            await asyncio.sleep(0.02)
        assert accounts.get("SYS-EMAIL") == "active", accounts
        assert accounts.get("SYS-VPN") == "pending", accounts  # stuck past deadline
        self._record("stuck_account_detected")
        await self._create_task(
            "EXCEPTION_HANDLING",
            {
                "reason": "account_provisioning_stuck",
                "system_id": "SYS-VPN",
                "employee_id": self.employee_id,
                "policy": "escalation",
            },
            _IT_ACTOR,
        )
        await self._wait_for_human()
        self.final_state = "waiting_for_human"

    async def _drive_policy_exception(self) -> None:
        """HR account for a non-manager: detect the violation, never verify it."""
        verification = await systems.verify_account(self.employee_id)
        assert verification["all_required_verified"] is True, verification
        assert verification["policy_violations"] == ["hr_account_for_non_manager"]
        assert verification["verified"]["SYS-HR"] is False  # never verified
        self._record("policy_violation_detected")
        await self._create_task(
            "CONFLICT_RESOLUTION",
            {
                "reason": "hr_account_for_non_manager",
                "system_id": "SYS-HR",
                "employee_id": self.employee_id,
                "policy": "hr-system-policy",
            },
            _MANAGER_ID,
        )
        await self._wait_for_human()
        # The violation blocks completion: the case is never verified/closed.
        self.final_state = "waiting_for_human"


async def run_pack_scenario(
    scenario_file: str,
    world_app: FastAPI,
    backend: httpx.AsyncClient,
) -> tuple[Scenario, ScenarioResult, ScenarioScore, ScriptedPackAgent]:
    """Run one pack scenario through the engines and return the score."""
    scenario = load_scenario(_SCENARIOS_DIR / scenario_file)
    holder: dict[str, ScriptedPackAgent] = {}

    def factory(fault_callbacks: tuple[Any, Any]) -> tuple[ScriptedPackAgent, asyncio.Event]:
        agent = ScriptedPackAgent(scenario.id, backend, fault_callbacks)
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
        retry_count=agent.retry_count,
        delegation_depth=0,
        case_ids=agent.case_ids,
        run_case_id=agent.case_id,
    )
    return scenario, result, score, agent


@pytest.mark.parametrize("scenario_file", PACK_SCENARIOS)
async def test_certification_pack_passes(
    world_app: FastAPI,
    systems_transport: httpx.ASGITransport,
    systems_backend_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    scenario_file: str,
) -> None:
    """Every pack scenario scores PASS against the SPEC §24 threshold."""
    del systems_transport, systems_backend_transport  # monkeypatched; implicit
    systems_agent = build_systems_agent()
    assert systems_agent.id == "systems-agent"

    (scenario, result, score, agent) = await run_pack_scenario(
        scenario_file, world_app, backend_client
    )

    assert score.passed is True, f"{scenario.id} failed: {score.model_dump_json()}"
    assert score.total >= score.threshold
    assert result.final_state in scenario.expected.allowed_final_states
    for event in scenario.expected.forbidden_events:
        assert event not in agent.timeline_events

    if scenario.id == "systems-03-service-unavailable":
        # The retry cap is asserted via the trajectory: exactly MAX_RETRIES
        # bounded retries, then escalation — never an unbounded loop.
        retries = agent.timeline_events.count("provisioning_retry")
        assert retries == _MAX_PROVISIONING_RETRIES <= scenario.max_retries
        assert "provisioning_escalated" in agent.timeline_events
        # Every applied DEC-05 fault targeted the declared mutation tool.
        assert result.faults_applied
        assert all(
            fault["tool"] == "provision_account" for fault in result.faults_applied
        )
