"""Hidden-scenario runner + DEC-14 distribution guard (SPEC §20/§28, Epic C batch 2a).

Two halves, deliberately in one module:

1. **Hidden runner.** Collects ``*.yaml`` from the hidden-scenario directory —
   ``AGENTLAB_HIDDEN_DIR`` if set, else ``scenarios/hidden/`` — and runs each
   through the SAME C.1 mechanism as the integration pack: the REAL onboarding
   coordinator (canned model turn, never a live LLM) plus scripted device and
   access loops driving the REAL MockWorld tools and backend APIs, scored by
   the REAL EvaluationEngine. On hosts without the private archive (fresh
   clones, CI) the directory is absent or empty and the runner SKIPS; on the
   platform host it RUNS and every hidden scenario must PASS.

2. **Distribution guard (DEC-14 [FINAL]).** Always runs, including CI:
   hidden scenarios never ship to participants — ``scenarios/hidden/`` is
   gitignored, the team-agent template carries no YAML and no hidden-scenario
   references, the knowledge corpora carry no hidden-scenario references, and
   the domain certification packs read only their own domain directories
   (never the hidden dir or the env override).

The C.1 harness pieces (fixtures, the canned model response) are imported
from :mod:`test_integration_scenario` rather than duplicated.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    # Mirror the integration harness: repo root on sys.path so the plain-code
    # ``agents`` namespace package resolves.
    sys.path.insert(0, str(_REPO_ROOT))

import agentlab.onboarding.coordinator as coordinator_module  # noqa: E402
from agents.access.tools import access  # noqa: E402
from agents.device.tools import device  # noqa: E402
from agentlab.backend.evaluation import EvaluationEngine  # noqa: E402
from agentlab.backend.scenarios import ScenarioEngine, load_scenario  # noqa: E402
from agentlab.backend.scenarios import faults as scenario_faults  # noqa: E402
from agentlab.onboarding import CoordinatorAgent  # noqa: E402
from agentlab.world import db as world_db  # noqa: E402
from agentlab.world.models import Employee, Identity, Manager  # noqa: E402

# Reuse the C.1 harness: the in-process app/client fixtures and the canned
# model response are exactly the integration harness's, imported (not copied).
from test_integration_scenario import (  # noqa: E402
    _canned_response,
    backend_app,  # noqa: F401  (fixture, pulled in for backend_client)
    backend_client,  # noqa: F401  (fixture)
    coordinator,  # noqa: F401  (fixture)
    domain_transports,  # noqa: F401  (fixture)
    world_app,  # noqa: F401  (fixture)
)

# --- hidden-scenario discovery (DEC-14) ---------------------------------------

_HIDDEN_DIR_ENV = "AGENTLAB_HIDDEN_DIR"
_DEFAULT_HIDDEN_DIR = _REPO_ROOT / "scenarios" / "hidden"


def _hidden_dir() -> Path:
    """The hidden-scenario directory: the env override or the repo default."""
    override = os.environ.get(_HIDDEN_DIR_ENV)
    return Path(override) if override else _DEFAULT_HIDDEN_DIR


def _hidden_scenario_files() -> list[Path]:
    directory = _hidden_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.yaml"))


_HIDDEN_FILES = _hidden_scenario_files()
_HIDDEN_PARAMS: list[Any] = [pytest.param(path, id=path.stem) for path in _HIDDEN_FILES]
if not _HIDDEN_PARAMS:  # fresh clone / CI: the archive is not on this host
    _HIDDEN_PARAMS = [pytest.param(None, id="no-hidden-scenarios")]

# --- the unknown-scenario cast (placeholders only, SPEC §20) -------------------

_TOKEN = "test-token"
_TIME_SCALE = 0.02  # the t=30 grant mutations land at ~0.6s in test time

_DEVICE_AGENT = "device-agent"
_ACCESS_AGENT = "access-agent"
_MANAGER_ID = "M1"
_MISSING_LOCATION_EMPLOYEE = "E201"  # address confirmation before delivery
_PRIVILEGED_RETRY_EMPLOYEE = "E202"  # privileged denial → new info → approval
_SUBSTITUTION_EMPLOYEE = "E203"  # standard SKU exhausted → approved substitute
_EMPLOYEES = [
    _MISSING_LOCATION_EMPLOYEE,
    _PRIVILEGED_RETRY_EMPLOYEE,
    _SUBSTITUTION_EMPLOYEE,
]
_CASES = {employee_id: f"ONB-{employee_id}" for employee_id in _EMPLOYEES}
_STANDARD_GROUP = "GRP-STANDARD"
_PRIVILEGED_GROUP = "GRP-PRIVILEGED"
_STANDARD_SKU = "macbook_pro_14"
_SUBSTITUTE_SKU = "macbook_air_15"
_START_DATE = "2026-08-31"  # Monday
_WORKFLOWS = ["device", "access"]
_READY_GOALS = {"employee_device_ready", "employee_access_ready"}


def _expected_state(scenario_id: str) -> dict[str, Any]:
    """The world state the run must reach, read back via truthful GETs."""
    assert scenario_id == "hidden-01-unknown-exceptions", scenario_id
    state: dict[str, Any] = {
        # 2 stocked by initial_state, both consumed (E201, E202); E203 took
        # the approved substitute, so the Air drops 7 -> 6.
        "macbook_pro_14_available": 0,
        "macbook_air_15_available": 6,
    }
    for employee_id in _EMPLOYEES:
        state[f"{employee_id}_device"] = "assigned"
        state[f"{employee_id}_order"] = "none"
        state[f"{employee_id}_grp_standard"] = "granted"
        state[f"{employee_id}_grp_privileged"] = (
            "granted" if employee_id == _PRIVILEGED_RETRY_EMPLOYEE else "none"
        )
    return state


def _provision_hidden_world() -> None:
    """Provision the rows the /simulation/load contract cannot create.

    The harness plays world operator here (never the agents): three pending
    employees with identities — E201's HR record deliberately missing its
    location — on top of the canonical seed (manager M1 already exists).
    """
    with world_db.session_scope() as session:
        for index, employee_id in enumerate(_EMPLOYEES, start=1):
            session.add(
                Employee(
                    id=employee_id,
                    name=f"Hidden Starter {index}",
                    role="Software Engineer",
                    location="" if employee_id == _MISSING_LOCATION_EMPLOYEE else "Amsterdam",
                    manager_id=_MANAGER_ID,
                    start_date=_START_DATE,
                    status="pending",
                )
            )
            session.add(
                Identity(
                    employee_id=employee_id,
                    username=f"hidden.starter{index}",
                    status="created",
                )
            )
        session.commit()


async def _read_final_world_state() -> dict[str, Any]:
    """Summarise final world state through the agents' truthful read tools."""
    inventory = await device.check_inventory(_EMPLOYEES[0])
    state: dict[str, Any] = {
        "macbook_pro_14_available": inventory["available"][_STANDARD_SKU],
        "macbook_air_15_available": inventory["available"][_SUBSTITUTE_SKU],
    }
    for employee_id in _EMPLOYEES:
        assignment = await device.get_device_assignment(employee_id)
        state[f"{employee_id}_device"] = (assignment.get("assigned_device") or {}).get(
            "status"
        )
        state[f"{employee_id}_order"] = (assignment.get("order") or {}).get(
            "status", "none"
        )
        requests = (await access.list_access_requests(employee_id)).get("requests", [])
        state[f"{employee_id}_grp_standard"] = next(
            (r["status"] for r in requests if r.get("group_id") == _STANDARD_GROUP),
            "none",
        )
        state[f"{employee_id}_grp_privileged"] = next(
            (r["status"] for r in requests if r.get("group_id") == _PRIVILEGED_GROUP),
            "none",
        )
    return state


class ScriptedHiddenAgent:
    """Composite agent under test: REAL coordinator + scripted domain loops.

    Same shape as the C.1 ScriptedIntegrationAgent: ``run`` drives the whole
    hidden flow (world provisioning, one case per employee, the real
    coordinator delegating both domains per case, scripted device/access loops
    reacting to the real delegation events) and records the trajectory events
    the hidden scenario expects. Nothing a real domain agent would do is
    stubbed; the human decisions are the scripted humans of the simulation.
    """

    def __init__(
        self,
        scenario_id: str,
        backend: httpx.AsyncClient,
        coordinator: CoordinatorAgent,
    ) -> None:
        self.scenario_id = scenario_id
        self.backend = backend
        self.coordinator = coordinator
        self.timeline_events: list[str] = []
        # (employee_id, event) pairs for ordered, per-employee assertions.
        self.detail_events: list[tuple[str, str]] = []
        self.final_state: str | None = None
        self.verdicts: dict[str, dict[str, Any]] = {}

    def _record(self, event: str, employee_id: str | None = None) -> None:
        self.timeline_events.append(event)
        if employee_id is not None:
            self.detail_events.append((employee_id, event))

    async def run(self, user_message: str) -> str:
        """Entry point used by the ScenarioEngine."""
        del user_message
        drivers = {"hidden-01-unknown-exceptions": self._drive_unknown_exceptions}
        driver = drivers.get(self.scenario_id)
        assert driver is not None, f"no scripted trajectory for {self.scenario_id}"
        await driver()
        return "done"

    # --- backend contract helpers --------------------------------------------

    async def _ack(self, workflow_id: str, agent_id: str) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/ack", headers={"X-Agent-Id": agent_id}
        )
        assert response.status_code == 200, response.text

    async def _report_waiting(self, workflow_id: str, agent_id: str) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/status",
            headers={"X-Agent-Id": agent_id},
            json={
                "workflow_id": workflow_id,
                "status": "waiting_for_human",
                "blockers": [],
            },
        )
        assert response.status_code == 200, response.text

    async def _create_task(
        self,
        task_id: str,
        workflow_id: str,
        agent_id: str,
        task_type: str,
        context: dict[str, Any],
    ) -> str:
        """Persist a HumanTask (HITL, SPEC §15) and record the event."""
        employee_id = context.get("employee_id")
        response = await self.backend.post(
            "/tasks",
            json={
                "human_task_id": task_id,
                "case_id": _CASES[employee_id],
                "workflow_id": workflow_id,
                "requested_by": agent_id,
                "requested_from": _MANAGER_ID,
                "type": task_type,
                "context": context,
                "allowed_actions": ["approve", "reject"],
                "status": "open",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        self._record("human_task_created", employee_id)
        return task_id

    async def _decide(self, task_id: str, decision: dict[str, Any]) -> httpx.Response:
        """Post the scripted human's decision (always the manager, DEC-10)."""
        return await self.backend.post(
            f"/tasks/{task_id}/decision",
            json={"decision": decision, "resolved_by": _MANAGER_ID},
        )

    async def _complete_verified(
        self, workflow_id: str, agent_id: str, employee_id: str
    ) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/complete",
            headers={"X-Agent-Id": agent_id},
            json={"verified": True},
        )
        assert response.status_code == 200, response.text
        self._record("outcome_verified", employee_id)

    async def _case_events(self, case_id: str) -> list[dict[str, Any]]:
        response = await self.backend.get(f"/cases/{case_id}/events")
        assert response.status_code == 200, response.text
        events: list[dict[str, Any]] = response.json()["events"]
        return events

    async def _await_delegations(self, target_agent_id: str) -> dict[str, str]:
        """Poll every case until all delegations to ``target_agent_id`` land.

        Gating on ALL delegations before acting is what keeps the trajectory
        deterministic: both loops then work strictly in employee order, which
        pins both the REQ-n access-request ids and the inventory consumption
        order (E201 and E202 drain the standard SKU before E203 checks).
        """
        delegations: dict[str, str] = {}
        for _ in range(600):  # 600 * 0.01s = 6s real-time budget
            for employee_id, case_id in _CASES.items():
                if employee_id in delegations:
                    continue
                for event in await self._case_events(case_id):
                    if (
                        event["type"] == "WORKFLOW_DELEGATED"
                        and event["payload"].get("target_agent_id") == target_agent_id
                    ):
                        delegations[employee_id] = event["workflow_id"]
                        break
            if len(delegations) == len(_EMPLOYEES):
                return delegations
            await asyncio.sleep(0.01)
        raise AssertionError(f"{target_agent_id} delegations never landed")

    # --- scenario driver -------------------------------------------------------

    async def _drive_unknown_exceptions(self) -> None:
        """Onboard E201..E203 with the real coordinator and both domains."""
        _provision_hidden_world()
        for employee_id, case_id in _CASES.items():
            response = await self.backend.post(
                "/cases",
                json={"case_id": case_id, "employee_id": employee_id, "context": {}},
            )
            assert response.status_code == 201, response.text

        loops = [
            asyncio.create_task(self._device_loop()),
            asyncio.create_task(self._access_loop()),
        ]
        try:
            verdicts = await asyncio.gather(
                *(
                    self.coordinator.run_onboarding(
                        employee_id, case_id=case_id, workflows=list(_WORKFLOWS)
                    )
                    for employee_id, case_id in _CASES.items()
                )
            )
        finally:
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)

        for employee_id, verdict in zip(_CASES, verdicts, strict=True):
            assert verdict["verdict"] == "READY", verdict
            assert set(verdict["ready_goals"]) == _READY_GOALS
            assert verdict["missing_goals"] == []
            self._record("readiness_verdict_ready", employee_id)
            self.verdicts[employee_id] = verdict

        # Canonical Event Store observations (SPEC §23 types), per case.
        for case_id in _CASES.values():
            for event in await self._case_events(case_id):
                self.timeline_events.append(event["type"])
        self.final_state = "completed"

    # --- scripted device agent -------------------------------------------------

    async def _device_loop(self) -> None:
        """Handle every device delegation strictly in employee order.

        E201 reserves first (2 -> 1), E202 second (1 -> 0), so E203 observes
        the standard SKU exhausted AT REQUEST TIME — no timed mutation, fully
        deterministic.
        """
        delegations = await self._await_delegations(_DEVICE_AGENT)
        for employee_id in _EMPLOYEES:
            await self._device_workflow(employee_id, delegations[employee_id])

    async def _device_workflow(self, employee_id: str, workflow_id: str) -> None:
        await self._ack(workflow_id, _DEVICE_AGENT)
        requirements = await device.get_employee_device_requirements(employee_id)
        assert "error" not in requirements, requirements
        if employee_id == _MISSING_LOCATION_EMPLOYEE:
            # Location policy: never guess a delivery address; ask a human.
            assert not requirements["location"], requirements
            self._record("location_missing_detected", employee_id)
            task_id = await self._create_task(
                f"HT-{employee_id}-address",
                workflow_id,
                _DEVICE_AGENT,
                "MISSING_INFORMATION",
                {"reason": "address_confirmation", "employee_id": employee_id},
            )
            await self._report_waiting(workflow_id, _DEVICE_AGENT)
            # Scripted human behavior: the manager confirms the address.
            decision = await self._decide(
                task_id,
                {"decision": "approve", "address": "1 Placeholder Lane, Amsterdam"},
            )
            assert decision.status_code == 200, decision.text
            self._record("address_confirmed", employee_id)
        else:
            assert requirements["location"] == "Amsterdam", requirements

        inventory = await device.check_inventory(employee_id)
        self._record("inventory_checked", employee_id)
        if employee_id == _SUBSTITUTION_EMPLOYEE:
            await self._device_substitution(employee_id, workflow_id, inventory)
        else:
            assert inventory["available"][_STANDARD_SKU] >= 1, inventory
            result = await device.reserve_device(employee_id, _STANDARD_SKU)
            assert result["reserved"] is True, result
            self._record("device_reserved", employee_id)

        assignment = await device.get_device_assignment(employee_id)
        assigned = assignment.get("assigned_device") or {}
        assert assigned.get("status") == "assigned", assignment
        if employee_id == _SUBSTITUTION_EMPLOYEE:
            assert assigned.get("sku") == _SUBSTITUTE_SKU, assignment
        self._record("delivery_verified", employee_id)
        await self._complete_verified(workflow_id, _DEVICE_AGENT, employee_id)

    async def _device_substitution(
        self, employee_id: str, workflow_id: str, inventory: dict[str, Any]
    ) -> None:
        """Standard SKU exhausted: substitute ONLY with manager approval."""
        assert inventory["available"][_STANDARD_SKU] == 0, inventory
        self._record("no_inventory_detected", employee_id)
        assert inventory["available"][_SUBSTITUTE_SKU] >= 1, inventory
        task_id = await self._create_task(
            f"HT-{employee_id}-substitution",
            workflow_id,
            _DEVICE_AGENT,
            "APPROVAL",
            {
                "reason": f"substitute {_SUBSTITUTE_SKU} for exhausted {_STANDARD_SKU}",
                "employee_id": employee_id,
            },
        )
        await self._report_waiting(workflow_id, _DEVICE_AGENT)
        decision = await self._decide(task_id, {"decision": "approve"})
        assert decision.status_code == 200, decision.text
        self._record("approval_granted", employee_id)
        result = await device.reserve_device(employee_id, _SUBSTITUTE_SKU)
        assert result["reserved"] is True, result
        self._record("substitute_reserved", employee_id)

    # --- scripted access agent -------------------------------------------------

    async def _access_loop(self) -> None:
        """Requests strictly in employee order (pinning REQ-1..REQ-4), then
        await the world's t=30 grant mutations via truthful read tools."""
        delegations = await self._await_delegations(_ACCESS_AGENT)
        for employee_id in _EMPLOYEES:
            await self._access_request_phase(employee_id, delegations[employee_id])

        pending = dict(delegations)
        for _ in range(600):  # 600 * 0.01s = 6s real-time budget
            if not pending:
                return
            for employee_id in list(pending):
                requests = (await access.list_access_requests(employee_id)).get(
                    "requests", []
                )
                if requests and all(r.get("status") == "granted" for r in requests):
                    self._record("access_granted", employee_id)
                    await self._complete_verified(
                        pending.pop(employee_id), _ACCESS_AGENT, employee_id
                    )
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed grant mutations never landed for {sorted(pending)}")

    async def _access_request_phase(self, employee_id: str, workflow_id: str) -> None:
        await self._ack(workflow_id, _ACCESS_AGENT)
        summary = await access.get_access_summary(employee_id)
        assert summary.get("identity") is not None, summary
        # No entitlements yet: access is earned through the flow, not assumed.
        assert summary.get("entitlements") == [], summary
        await self._request_group(employee_id, _STANDARD_GROUP, "onboarding baseline")
        if employee_id == _PRIVILEGED_RETRY_EMPLOYEE:
            await self._privileged_denied_then_new_info(employee_id, workflow_id)

    async def _request_group(
        self, employee_id: str, group_id: str, description: str
    ) -> None:
        result = await access.request_group_access(employee_id, group_id, description)
        assert result["requested"] is True, result
        self._record("access_requested", employee_id)
        self.detail_events.append((employee_id, f"access_requested:{group_id}"))

    async def _privileged_denied_then_new_info(
        self, employee_id: str, workflow_id: str
    ) -> None:
        """Privileged-group request DENIED once, then re-requested with NEW
        justification and approved (request-resolution policy: a denial is
        final for the stated justification — never retry without new info)."""
        first_task = await self._create_task(
            f"HT-{employee_id}-privileged-1",
            workflow_id,
            _ACCESS_AGENT,
            "APPROVAL",
            {
                "reason": "privileged group requires manager approval",
                "group_id": _PRIVILEGED_GROUP,
                "employee_id": employee_id,
                "justification": "standard onboarding",
            },
        )
        await self._report_waiting(workflow_id, _ACCESS_AGENT)
        # Scripted human behavior: the manager denies the thin justification.
        denied = await self._decide(first_task, {"decision": "reject"})
        assert denied.status_code == 200, denied.text
        self._record("approval_denied", employee_id)

        # New information arrives (scripted): the retry restates the request
        # with a fresh justification — a different request, not a blind retry.
        second_task = await self._create_task(
            f"HT-{employee_id}-privileged-2",
            workflow_id,
            _ACCESS_AGENT,
            "APPROVAL",
            {
                "reason": "privileged group requires manager approval",
                "group_id": _PRIVILEGED_GROUP,
                "employee_id": employee_id,
                "justification": "new information: on-call rotation duty",
                "previous_task": first_task,
            },
        )
        await self._report_waiting(workflow_id, _ACCESS_AGENT)
        approved = await self._decide(second_task, {"decision": "approve"})
        assert approved.status_code == 200, approved.text
        self._record("approval_granted", employee_id)
        # Policy order: the world request goes out only AFTER the approval.
        await self._request_group(
            employee_id, _PRIVILEGED_GROUP, "on-call rotation duty (new justification)"
        )


@pytest.mark.parametrize("scenario_path", _HIDDEN_PARAMS)
async def test_hidden_scenario_passes(
    world_app: FastAPI,
    domain_transports: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    coordinator: CoordinatorAgent,
    monkeypatch: pytest.MonkeyPatch,
    scenario_path: Path | None,
) -> None:
    """Every hidden scenario scores PASS against the SPEC §24 threshold.

    Skips on hosts without the private archive (fresh clone / CI); on the
    platform host the archive is present (or ``AGENTLAB_HIDDEN_DIR`` points
    at it) and every hidden scenario must pass.
    """
    del domain_transports  # monkeypatched; used implicitly by the tools
    if scenario_path is None:
        pytest.skip("no hidden scenarios on this host (fresh clone/CI)")
    scenario = load_scenario(scenario_path)
    if scenario.id == _CHAOS_SCENARIO_ID:
        # The chaos scenario carries DEC-05 tool faults and its own scripted
        # cast (C.2b); it runs through the chaos harness below. The dedicated
        # chaos test adds the readiness-map and audit-trail assertions on top.
        await _run_chaos_and_assert_pass(
            world_app, backend_client, coordinator, monkeypatch, scenario
        )
        return
    monkeypatch.setattr(coordinator_module, "POLL_INTERVAL_SECONDS", 0.02)
    coordinator.agent.before_model_callback = _canned_response

    holder: dict[str, ScriptedHiddenAgent] = {}

    def factory(
        fault_callbacks: tuple[Any, Any],
    ) -> tuple[ScriptedHiddenAgent, asyncio.Event]:
        del fault_callbacks  # no tool faults in the hidden batch-2a pack
        agent = ScriptedHiddenAgent(scenario.id, backend_client, coordinator)
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
    final_state = await _read_final_world_state()
    # Multi-case run: the no-case-contamination assertion (single run_case_id)
    # does not apply, so case ids are intentionally not supplied.
    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state=final_state,
        expected_state=_expected_state(scenario.id),
        retry_count=0,
        delegation_depth=1,  # coordinator -> domain agent
    )

    assert score.passed is True, f"{scenario.id} failed: {score.model_dump_json()}"
    assert score.total >= score.threshold
    assert result.final_state == "completed"
    assert result.final_state in scenario.expected.allowed_final_states
    for event in scenario.expected.forbidden_events:
        assert event not in agent.timeline_events

    # E202 ordering: denial first, then the approval, and the privileged
    # world request strictly AFTER the approval (never before).
    employee_events = [
        event
        for employee_id, event in agent.detail_events
        if employee_id == _PRIVILEGED_RETRY_EMPLOYEE
    ]
    denied_at = employee_events.index("approval_denied")
    granted_at = employee_events.index("approval_granted")
    request_at = employee_events.index(f"access_requested:{_PRIVILEGED_GROUP}")
    assert denied_at < granted_at < request_at

    # Every employee's readiness verdict is READY with all outcomes verified.
    assert set(agent.verdicts) == set(_EMPLOYEES)
    for verdict in agent.verdicts.values():
        assert verdict["verdict"] == "READY"
        assert verdict["missing_goals"] == []

    # Final world state spot-checks beyond the scored diff.
    assert final_state["macbook_pro_14_available"] == 0
    assert final_state["macbook_air_15_available"] == 6
    assert final_state[f"{_PRIVILEGED_RETRY_EMPLOYEE}_grp_privileged"] == "granted"
    assert final_state[f"{_SUBSTITUTION_EMPLOYEE}_device"] == "assigned"


# --- the chaos-scenario cast (placeholders only, SPEC §20, Epic C batch 2b) ----

_CHAOS_SCENARIO_ID = "hidden-02-chaos-monday-starters"
_CHAOS_SCENARIO_STEM = "02_chaos_monday_starters"
_CHAOS_EMPLOYEES = [f"E3{index:02d}" for index in range(1, 13)]  # E301..E312
_CHAOS_CASES = {employee_id: f"ONB-{employee_id}" for employee_id in _CHAOS_EMPLOYEES}
_CHAOS_NEW_MANAGER = "M2"  # E306's manager after the t=10 mutation

_CHAOS_INVENTORY_EMPLOYEE = "E303"  # standard pool consumed + substitute zeroed
_CHAOS_PRIVILEGED_EMPLOYEE = "E304"  # privileged approval -> ready
_CHAOS_MANAGER_CHANGE_EMPLOYEE = "E306"  # manager flips M1 -> M2 mid-onboarding
_CHAOS_LYING_EMPLOYEE = "E308"  # reserve_device lies about success
_CHAOS_CONFLICT_EMPLOYEE = "E309"  # corpus says pre-granted, world says missing
_CHAOS_UNANSWERED_EMPLOYEE = "E310"  # the approval HumanTask never answered
_CHAOS_TIMEOUT_EMPLOYEE = "E311"  # request_group_access times out
_FAKE_DEVICE_ID = "DEV-FAKE"  # the lying fault's fake success shape (DEC-05)

# The readiness map the fault mix mandates (see the YAML header for the policy
# citations): 9 READY, 3 NOT READY, each NOT READY with its missing goal and
# the machine-readable blocker code the escalation policies require.
_CHAOS_NOT_READY: dict[str, tuple[str, str]] = {
    _CHAOS_INVENTORY_EMPLOYEE: ("employee_device_ready", "NO_INVENTORY"),
    _CHAOS_UNANSWERED_EMPLOYEE: ("employee_access_ready", "APPROVAL_SLA_TIMEOUT"),
    _CHAOS_TIMEOUT_EMPLOYEE: ("employee_access_ready", "TOOL_TIMEOUT"),
}
_CHAOS_READY = [e for e in _CHAOS_EMPLOYEES if e not in _CHAOS_NOT_READY]
# Access workflows that never complete (blocked, escalated): excluded from the
# grant-completion phase. E303's access path is clean and completes normally.
_CHAOS_ACCESS_INCOMPLETE = {_CHAOS_UNANSWERED_EMPLOYEE, _CHAOS_TIMEOUT_EMPLOYEE}

# Deterministic processing orders (both loops gate on ALL delegations, then
# work strictly in these orders): E308 first on the device side so its lying
# reserve is the only reserve inside the lie window; E311 first on the access
# side so its three timed-out attempts are the only requests inside the
# timeout window; E303 last on the device side so the standard pool (11 units)
# is fully consumed by the other eleven starters when it checks; E310 last on
# the access side so its slow unanswered-approval SLA cannot delay E312's
# request past the timed grant mutations (every REQ row must exist before the
# grants land — a mutation against a missing row is a world-side no-op).
_CHAOS_DEVICE_ORDER = [
    _CHAOS_LYING_EMPLOYEE,
    "E301",
    "E302",
    "E304",
    "E305",
    "E306",
    "E307",
    "E309",
    "E310",
    "E311",
    "E312",
    _CHAOS_INVENTORY_EMPLOYEE,
]
_CHAOS_ACCESS_ORDER = [
    _CHAOS_TIMEOUT_EMPLOYEE,
    "E301",
    "E302",
    "E303",
    "E304",
    "E305",
    "E306",
    "E307",
    "E308",
    "E309",
    "E312",
    _CHAOS_UNANSWERED_EMPLOYEE,
]
assert sorted(_CHAOS_DEVICE_ORDER) == sorted(_CHAOS_EMPLOYEES)
assert sorted(_CHAOS_ACCESS_ORDER) == sorted(_CHAOS_EMPLOYEES)


def _provision_chaos_world() -> None:
    """Provision the rows the /simulation/load contract cannot create.

    The harness plays world operator here (never the agents): twelve pending
    Monday starters with identities — all managed by M1 at provisioning time —
    plus manager M2, the manager E306's HR record flips to at t=10 (M1 comes
    from the canonical seed). No entitlements: access is earned through the
    flow.
    """
    with world_db.session_scope() as session:
        session.add(
            Manager(id=_CHAOS_NEW_MANAGER, name="Second Manager", email="m2@example.test")
        )
        for index, employee_id in enumerate(_CHAOS_EMPLOYEES, start=1):
            session.add(
                Employee(
                    id=employee_id,
                    name=f"Chaos Starter {index}",
                    role="Software Engineer",
                    location="Amsterdam",
                    manager_id=_MANAGER_ID,
                    start_date=_START_DATE,
                    status="pending",
                )
            )
            session.add(
                Identity(
                    employee_id=employee_id,
                    username=f"chaos.starter{index}",
                    status="created",
                )
            )
        session.commit()


def _expected_chaos_state() -> dict[str, Any]:
    """The world state the chaos run must reach, read back via truthful GETs."""
    state: dict[str, Any] = {
        # 11 stocked by initial_state, all consumed (everyone but E303); the
        # substitute pool is zeroed by the t=60 mutation, never consumed.
        "macbook_pro_14_available": 0,
        "macbook_air_15_available": 0,
    }
    for employee_id in _CHAOS_EMPLOYEES:
        state[f"{employee_id}_device"] = (
            None if employee_id == _CHAOS_INVENTORY_EMPLOYEE else "assigned"
        )
        state[f"{employee_id}_order"] = "none"
        state[f"{employee_id}_grp_standard"] = (
            "none" if employee_id == _CHAOS_TIMEOUT_EMPLOYEE else "granted"
        )
        state[f"{employee_id}_grp_privileged"] = (
            "granted"
            if employee_id in {_CHAOS_PRIVILEGED_EMPLOYEE, _CHAOS_MANAGER_CHANGE_EMPLOYEE}
            else "none"
        )
    return state


async def _read_chaos_world_state() -> dict[str, Any]:
    """Summarise final world state through the agents' truthful read tools."""
    inventory = await device.check_inventory(_CHAOS_EMPLOYEES[0])
    state: dict[str, Any] = {
        "macbook_pro_14_available": inventory["available"][_STANDARD_SKU],
        "macbook_air_15_available": inventory["available"][_SUBSTITUTE_SKU],
    }
    for employee_id in _CHAOS_EMPLOYEES:
        assignment = await device.get_device_assignment(employee_id)
        state[f"{employee_id}_device"] = (assignment.get("assigned_device") or {}).get(
            "status"
        )
        state[f"{employee_id}_order"] = (assignment.get("order") or {}).get(
            "status", "none"
        )
        requests = (await access.list_access_requests(employee_id)).get("requests", [])
        state[f"{employee_id}_grp_standard"] = next(
            (r["status"] for r in requests if r.get("group_id") == _STANDARD_GROUP),
            "none",
        )
        state[f"{employee_id}_grp_privileged"] = next(
            (r["status"] for r in requests if r.get("group_id") == _PRIVILEGED_GROUP),
            "none",
        )
    return state


async def _heal_fault_window(tool: str, applications: int) -> None:
    """Close a transient fault window after it has provably fired.

    The DEC-05 fault primitives arm PERMANENTLY at their scheduled time; the
    chaos scenario models TRANSIENT faults (the agent must detect, then
    recover). Closing the window is the world operator's share of the
    simulation — the same role the harness plays for row provisioning. The
    faults module has no public disarm API, so this removes the armed entry
    directly, deliberately leaving the applied-fault record intact.
    """
    for _ in range(2400):  # 2400 * 0.005s = 12s real-time budget
        applied = [f for f in scenario_faults.snapshot_applied_faults() if f["tool"] == tool]
        if len(applied) >= applications:
            scenario_faults._active_faults.pop(tool, None)  # world-operator heal
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"fault window for {tool!r} never fired {applications}x")


class ScriptedChaosAgent:
    """Composite chaos agent under test: REAL coordinator + scripted domain
    loops, with the DEC-05 tool faults in the loop.

    Same shape as the C.2a ScriptedHiddenAgent, plus one difference: every
    mutation-tool call is routed through the engine-armed fault callbacks,
    exactly as the ADK runtime would — a ``success_without_state_change``
    fault short-circuits the call with a success-shaped fake, a ``timeout``
    fault raises. Reads are never faulted (DEC-05) and stay truthful, which is
    what lets the scripted agents DETECT the lies. Nothing a real domain agent
    would do is stubbed; the human decisions (and non-decisions) are the
    scripted humans of the simulation.
    """

    def __init__(
        self,
        backend: httpx.AsyncClient,
        coordinator: CoordinatorAgent,
        world_app: FastAPI,
        fault_callbacks: tuple[Any, Any],
    ) -> None:
        self.backend = backend
        self.coordinator = coordinator
        self._world_app = world_app
        self._before_tool, self._after_tool = fault_callbacks
        self.timeline_events: list[str] = []
        # (employee_id, event) pairs for ordered, per-employee assertions.
        self.detail_events: list[tuple[str, str]] = []
        self.final_state: str | None = None
        self.verdicts: dict[str, dict[str, Any]] = {}

    def _record(self, event: str, employee_id: str | None = None) -> None:
        self.timeline_events.append(event)
        if employee_id is not None:
            self.detail_events.append((employee_id, event))

    async def run(self, user_message: str) -> str:
        """Entry point used by the ScenarioEngine."""
        del user_message
        await self._drive_chaos()
        return "done"

    # --- backend contract helpers --------------------------------------------

    async def _ack(self, workflow_id: str, agent_id: str) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/ack", headers={"X-Agent-Id": agent_id}
        )
        assert response.status_code == 200, response.text

    async def _report_waiting(self, workflow_id: str, agent_id: str) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/status",
            headers={"X-Agent-Id": agent_id},
            json={
                "workflow_id": workflow_id,
                "status": "waiting_for_human",
                "blockers": [],
            },
        )
        assert response.status_code == 200, response.text

    async def _escalate_blocked(
        self,
        employee_id: str,
        workflow_id: str,
        agent_id: str,
        code: str,
        description: str,
    ) -> None:
        """Escalate per policy: report BLOCKED with a machine-readable blocker.

        This is the escalation handoff the access/devices escalation policies
        mandate ("report the workflow blocked with a machine-readable blocker
        code rather than improvising a workaround"). The coordinator's blocker
        reconciliation takes it from there (ops-lead HITL task, SLA, escalate).
        """
        self._record("escalation_reported", employee_id)
        response = await self.backend.post(
            f"/workflows/{workflow_id}/status",
            headers={"X-Agent-Id": agent_id},
            json={
                "workflow_id": workflow_id,
                "status": "blocked",
                "blockers": [{"code": code, "description": description}],
            },
        )
        assert response.status_code == 200, response.text

    async def _create_task(
        self,
        task_id: str,
        workflow_id: str,
        agent_id: str,
        requested_from: str,
        task_type: str,
        context: dict[str, Any],
    ) -> str:
        """Persist a HumanTask (HITL, SPEC §15) and record the event."""
        employee_id = context.get("employee_id")
        response = await self.backend.post(
            "/tasks",
            json={
                "human_task_id": task_id,
                "case_id": _CHAOS_CASES[employee_id],
                "workflow_id": workflow_id,
                "requested_by": agent_id,
                "requested_from": requested_from,
                "type": task_type,
                "context": context,
                "allowed_actions": ["approve", "reject"],
                "status": "open",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        self._record("human_task_created", employee_id)
        return task_id

    async def _decide(
        self, task_id: str, decision: dict[str, Any], resolved_by: str
    ) -> httpx.Response:
        """Post a scripted human's decision (DEC-10: only requested_from may)."""
        return await self.backend.post(
            f"/tasks/{task_id}/decision",
            json={"decision": decision, "resolved_by": resolved_by},
        )

    async def _complete_verified(
        self, workflow_id: str, agent_id: str, employee_id: str
    ) -> None:
        response = await self.backend.post(
            f"/workflows/{workflow_id}/complete",
            headers={"X-Agent-Id": agent_id},
            json={"verified": True},
        )
        assert response.status_code == 200, response.text
        self._record("outcome_verified", employee_id)

    async def _case_events(self, case_id: str) -> list[dict[str, Any]]:
        response = await self.backend.get(f"/cases/{case_id}/events")
        assert response.status_code == 200, response.text
        events: list[dict[str, Any]] = response.json()["events"]
        return events

    async def _await_delegations(self, target_agent_id: str) -> dict[str, str]:
        """Poll every case until all delegations to ``target_agent_id`` land.

        Gating on ALL delegations before acting keeps the trajectory
        deterministic: both loops then work strictly in their pinned orders,
        which fixes the REQ-n access-request ids and the inventory consumption
        order (the eleven clean device paths drain the standard SKU before
        E303 checks).
        """
        delegations: dict[str, str] = {}
        for _ in range(600):  # 600 * 0.01s = 6s real-time budget
            for employee_id, case_id in _CHAOS_CASES.items():
                if employee_id in delegations:
                    continue
                for event in await self._case_events(case_id):
                    if (
                        event["type"] == "WORKFLOW_DELEGATED"
                        and event["payload"].get("target_agent_id") == target_agent_id
                    ):
                        delegations[employee_id] = event["workflow_id"]
                        break
            if len(delegations) == len(_CHAOS_EMPLOYEES):
                return delegations
            await asyncio.sleep(0.01)
        raise AssertionError(f"{target_agent_id} delegations never landed")

    async def _get_manager_id(self, employee_id: str) -> str:
        """Read the employee's CURRENT manager via the shared employees route.

        ``/world/employees`` is shared across registered agents (DEC-07); the
        access domain has no wrapped tool for it, so the scripted agent reads
        it directly under its own identity — what a real access agent needing
        the current approver would do.
        """
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._world_app),
            base_url="http://mockworld",
            headers={"X-Agent-Id": _ACCESS_AGENT},
        ) as client:
            response = await client.get(f"/world/employees/{employee_id}")
        assert response.status_code == 200, response.text
        manager_id = response.json().get("manager_id")
        assert isinstance(manager_id, str), response.json()
        return manager_id

    # --- fault-routed mutation tools -------------------------------------------

    async def _guarded_mutation(
        self, tool_name: str, args: dict[str, Any], call: Any
    ) -> dict[str, Any]:
        """Invoke a mutation tool through the armed fault filter (ADK semantics).

        A ``success_without_state_change`` fault short-circuits the real call
        and returns its fake success shape; a ``timeout`` fault raises
        ``TimeoutError`` before the world is ever reached.
        """
        fake = await self._before_tool(SimpleNamespace(name=tool_name), args, None)
        if fake is not None:
            return fake
        result: dict[str, Any] = await call()
        return result

    # --- scenario driver -------------------------------------------------------

    async def _drive_chaos(self) -> None:
        """Onboard E301..E312 with the real coordinator and both domains."""
        _provision_chaos_world()
        for employee_id, case_id in _CHAOS_CASES.items():
            response = await self.backend.post(
                "/cases",
                json={"case_id": case_id, "employee_id": employee_id, "context": {}},
            )
            assert response.status_code == 201, response.text

        loops = [
            asyncio.create_task(self._device_loop()),
            asyncio.create_task(self._access_loop()),
        ]
        try:
            # Bounded so a dead scripted loop fails the test instead of hanging.
            verdicts = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        self.coordinator.run_onboarding(
                            employee_id, case_id=case_id, workflows=list(_WORKFLOWS)
                        )
                        for employee_id, case_id in _CHAOS_CASES.items()
                    )
                ),
                timeout=60,
            )
        finally:
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)

        for employee_id, verdict in zip(_CHAOS_EMPLOYEES, verdicts, strict=True):
            self.verdicts[employee_id] = verdict
            self._record(
                "readiness_verdict_ready"
                if verdict["verdict"] == "READY"
                else "readiness_verdict_not_ready",
                employee_id,
            )

        # Canonical Event Store observations (SPEC §23 types), per case.
        for case_id in _CHAOS_CASES.values():
            for event in await self._case_events(case_id):
                self.timeline_events.append(event["type"])
        self.final_state = "completed"

    # --- scripted device agent -------------------------------------------------

    async def _device_loop(self) -> None:
        """Handle every device delegation strictly in the pinned device order."""
        delegations = await self._await_delegations(_DEVICE_AGENT)
        for employee_id in _CHAOS_DEVICE_ORDER:
            await self._device_workflow(employee_id, delegations[employee_id])

    async def _device_workflow(self, employee_id: str, workflow_id: str) -> None:
        await self._ack(workflow_id, _DEVICE_AGENT)
        requirements = await device.get_employee_device_requirements(employee_id)
        assert "error" not in requirements, requirements
        assert requirements["location"] == "Amsterdam", requirements
        inventory = await device.check_inventory(employee_id)
        self._record("inventory_checked", employee_id)
        if employee_id == _CHAOS_INVENTORY_EMPLOYEE:
            await self._device_inventory_exhausted(employee_id, workflow_id, inventory)
            return
        assert inventory["available"][_STANDARD_SKU] >= 1, inventory
        if employee_id == _CHAOS_LYING_EMPLOYEE:
            await self._device_lying_reserve(employee_id)
        else:
            result = await self._guarded_mutation(
                "reserve_device",
                {"employee_id": employee_id, "sku": _STANDARD_SKU},
                lambda: device.reserve_device(employee_id, _STANDARD_SKU),
            )
            assert result["reserved"] is True, result
            self._record("device_reserved", employee_id)
        assignment = await device.get_device_assignment(employee_id)
        assigned = assignment.get("assigned_device") or {}
        assert assigned.get("status") == "assigned", assignment
        self._record("delivery_verified", employee_id)
        await self._complete_verified(workflow_id, _DEVICE_AGENT, employee_id)

    async def _device_lying_reserve(self, employee_id: str) -> None:
        """E308: reserve_device returns a success-shaped fake — world unchanged.

        Verification policy: never trust a mutation's return value; confirm
        with a truthful read (get_device_assignment). The lie is detected, then
        the reserve is retried until the world actually changes (the chaos
        window is transient — the world operator closes it once the lie has
        provably fired).
        """
        lie_detected = False
        for _attempt in range(100):  # 100 * 0.02s = 2s budget
            result = await self._guarded_mutation(
                "reserve_device",
                {"employee_id": employee_id, "sku": _STANDARD_SKU},
                lambda: device.reserve_device(employee_id, _STANDARD_SKU),
            )
            assert result["reserved"] is True, result
            assignment = await device.get_device_assignment(employee_id)
            if (assignment.get("assigned_device") or {}).get("status") == "assigned":
                if not lie_detected:
                    raise AssertionError("the lying-provisioning fault never fired")
                self._record("device_reserved", employee_id)
                return
            if not lie_detected:
                lie_detected = True
                self._record("provisioning_lie_detected", employee_id)
            await asyncio.sleep(0.02)
        raise AssertionError("reserve never materialised in the world")

    async def _device_inventory_exhausted(
        self, employee_id: str, workflow_id: str, inventory: dict[str, Any]
    ) -> None:
        """E303: both pools exhausted — substitute policy cannot fire, escalate.

        The standard pool was consumed to zero by the other eleven starters;
        the substitute pool is zeroed by the t=60 world mutation (re-confirmed
        via truthful re-reads — stock is volatile during the Monday rush).
        Substitution policy (knowledge/devices/inventory-substitution.md)
        cannot fire on zero substitute stock; the escalation policy
        (knowledge/devices/escalation.md, exhausted >3 days -> escalate)
        compresses in simulation to escalating through the workflow.
        """
        assert inventory["available"][_STANDARD_SKU] == 0, inventory
        self._record("no_inventory_detected", employee_id)
        for _ in range(600):  # 6s budget for the t=60 substitute zeroing
            inventory = await device.check_inventory(employee_id)
            if inventory["available"][_SUBSTITUTE_SKU] == 0:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("timed inventory-zeroing mutation never landed")
        self._record("inventory_exhausted_detected", employee_id)
        await self._escalate_blocked(
            employee_id,
            workflow_id,
            _DEVICE_AGENT,
            "NO_INVENTORY",
            "standard and substitute SKUs exhausted; escalated per devices "
            "escalation policy",
        )

    # --- scripted access agent -------------------------------------------------

    async def _access_loop(self) -> None:
        """Requests strictly in the pinned access order (fixing REQ-1..REQ-13),
        then await the world's t=120 grant mutations via truthful read tools."""
        delegations = await self._await_delegations(_ACCESS_AGENT)
        for employee_id in _CHAOS_ACCESS_ORDER:
            await self._access_request_phase(employee_id, delegations[employee_id])

        pending = {
            employee_id: workflow_id
            for employee_id, workflow_id in delegations.items()
            if employee_id not in _CHAOS_ACCESS_INCOMPLETE
        }
        for _ in range(600):  # 600 * 0.01s = 6s real-time budget
            if not pending:
                return
            for employee_id in list(pending):
                requests = (await access.list_access_requests(employee_id)).get(
                    "requests", []
                )
                if requests and all(r.get("status") == "granted" for r in requests):
                    self._record("access_granted", employee_id)
                    await self._complete_verified(
                        pending.pop(employee_id), _ACCESS_AGENT, employee_id
                    )
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed grant mutations never landed for {sorted(pending)}")

    async def _access_request_phase(self, employee_id: str, workflow_id: str) -> None:
        await self._ack(workflow_id, _ACCESS_AGENT)
        summary = await access.get_access_summary(employee_id)
        assert summary.get("identity") is not None, summary
        # No entitlements yet: access is earned through the flow, not assumed.
        assert summary.get("entitlements") == [], summary
        if employee_id == _CHAOS_TIMEOUT_EMPLOYEE:
            await self._access_tool_timeout(employee_id, workflow_id)
            return
        if employee_id == _CHAOS_CONFLICT_EMPLOYEE:
            # knowledge/access/standard-access-policy.md claims GRP-STANDARD is
            # pre-granted by HR provisioning; the truthful summary shows the
            # entitlement MISSING. Trust world state over the corpus: request
            # it (a no-op trust of the corpus would leave E309 without access).
            self._record("knowledge_conflict_detected", employee_id)
        await self._request_group(employee_id, _STANDARD_GROUP, "onboarding baseline")
        if employee_id == _CHAOS_PRIVILEGED_EMPLOYEE:
            await self._privileged_with_approval(employee_id, workflow_id)
        elif employee_id == _CHAOS_MANAGER_CHANGE_EMPLOYEE:
            await self._privileged_after_manager_change(employee_id, workflow_id)
        elif employee_id == _CHAOS_UNANSWERED_EMPLOYEE:
            await self._privileged_unanswered(employee_id, workflow_id)

    async def _request_group(
        self, employee_id: str, group_id: str, description: str
    ) -> None:
        """Request a group, riding out the tail of the transient timeout window.

        The engine arms a request_group_access timeout fault at t=0; the world
        operator closes it after E311's three attempts. Requests after the
        window are clean; a request that catches the window's tail retries
        (bounded) — the normal transient-fault response.
        """
        for _attempt in range(60):  # 60 * 0.02s = 1.2s of window tolerance
            try:
                result = await self._guarded_mutation(
                    "request_group_access",
                    {
                        "employee_id": employee_id,
                        "group_id": group_id,
                        "description": description,
                    },
                    lambda: access.request_group_access(employee_id, group_id, description),
                )
            except TimeoutError:
                await asyncio.sleep(0.02)
                continue
            assert result["requested"] is True, result
            self._record("access_requested", employee_id)
            self.detail_events.append((employee_id, f"access_requested:{group_id}"))
            return
        raise AssertionError(f"request_group_access still timing out for {employee_id}")

    async def _access_tool_timeout(self, employee_id: str, workflow_id: str) -> None:
        """E311: the request tool times out through the whole retry budget.

        The timeout window outlasts the agent's bounded retries (three
        attempts — the DEC-08 MAX_RETRIES bound), so the access escalation
        policy fires: never improvise around an unresolvable blocker; escalate
        through the workflow with a machine-readable blocker code.
        """
        for _attempt in range(3):  # MAX_RETRIES
            try:
                await self._guarded_mutation(
                    "request_group_access",
                    {
                        "employee_id": employee_id,
                        "group_id": _STANDARD_GROUP,
                        "description": "onboarding baseline",
                    },
                    lambda: access.request_group_access(
                        employee_id, _STANDARD_GROUP, "onboarding baseline"
                    ),
                )
            except TimeoutError:
                continue
            raise AssertionError(
                "E311's request unexpectedly succeeded inside the timeout window"
            )
        self._record("tool_timeout_detected", employee_id)
        await self._escalate_blocked(
            employee_id,
            workflow_id,
            _ACCESS_AGENT,
            "TOOL_TIMEOUT",
            "request_group_access timed out repeatedly; escalated per access "
            "escalation policy",
        )

    async def _privileged_with_approval(
        self, employee_id: str, workflow_id: str
    ) -> None:
        """E304: privileged-group policy — manager approval BEFORE the request."""
        task_id = await self._create_task(
            f"HT-{employee_id}-privileged",
            workflow_id,
            _ACCESS_AGENT,
            _MANAGER_ID,
            "APPROVAL",
            {
                "reason": "privileged group requires manager approval",
                "group_id": _PRIVILEGED_GROUP,
                "employee_id": employee_id,
            },
        )
        await self._report_waiting(workflow_id, _ACCESS_AGENT)
        # Scripted human behavior: the manager (requested_from, DEC-10) approves.
        decision = await self._decide(task_id, {"decision": "approve"}, _MANAGER_ID)
        assert decision.status_code == 200, decision.text
        self._record("approval_granted", employee_id)
        await self._request_group(employee_id, _PRIVILEGED_GROUP, "onboarding privileged")

    async def _privileged_after_manager_change(
        self, employee_id: str, workflow_id: str
    ) -> None:
        """E306: the manager flips M1 -> M2 mid-onboarding (t=10 mutation).

        The approval task is opened with the CURRENT manager as requested_from.
        The OLD manager's approval attempt is rejected 403 (DEC-10, mirroring
        access pack 03); the new manager's approval stands, and only then does
        the world request go out.
        """
        for _ in range(600):  # 6s budget for the t=10 manager mutation
            if await self._get_manager_id(employee_id) == _CHAOS_NEW_MANAGER:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("manager-change mutation never landed")
        self._record("manager_change_detected", employee_id)
        task_id = await self._create_task(
            f"HT-{employee_id}-privileged",
            workflow_id,
            _ACCESS_AGENT,
            _CHAOS_NEW_MANAGER,
            "APPROVAL",
            {
                "reason": "privileged group requires manager approval",
                "group_id": _PRIVILEGED_GROUP,
                "employee_id": employee_id,
            },
        )
        await self._report_waiting(workflow_id, _ACCESS_AGENT)
        # Scripted human behavior: the OLD manager attempts to approve anyway.
        rejected = await self._decide(task_id, {"decision": "approve"}, _MANAGER_ID)
        assert rejected.status_code == 403, rejected.text
        self._record("unauthorized_approval_rejected", employee_id)
        # The NEW manager (requested_from) approves.
        approved = await self._decide(task_id, {"decision": "approve"}, _CHAOS_NEW_MANAGER)
        assert approved.status_code == 200, approved.text
        self._record("approval_granted", employee_id)
        await self._request_group(
            employee_id,
            _PRIVILEGED_GROUP,
            "onboarding privileged (approved by the current manager)",
        )

    async def _privileged_unanswered(self, employee_id: str, workflow_id: str) -> None:
        """E310: the approval HumanTask is NEVER answered (SPEC §20).

        Scripted human behavior: the manager never decides. The domain agent's
        own approval SLA expires first; the access escalation policy ("if a
        manager approval never arrives within the human-task SLA, escalate to
        the onboarding coordinator by reporting a blocker; do not proceed
        without the approval") fires. The pending HumanTask row stays open as
        audit-trail evidence; the coordinator's blocker reconciliation (ops-lead
        task, SLA timeout, escalate) runs on top.
        """
        task_id = await self._create_task(
            f"HT-{employee_id}-privileged",
            workflow_id,
            _ACCESS_AGENT,
            _MANAGER_ID,
            "APPROVAL",
            {
                "reason": "privileged group requires manager approval",
                "group_id": _PRIVILEGED_GROUP,
                "employee_id": employee_id,
            },
        )
        await self._report_waiting(workflow_id, _ACCESS_AGENT)
        for _ in range(75):  # 75 * 0.02s = 1.5s scripted approval SLA
            task = await self.backend.get(f"/tasks/{task_id}")
            assert task.status_code == 200, task.text
            if task.json()["status"] == "resolved":
                raise AssertionError(
                    "the scenario requires this approval to stay unanswered"
                )
            await asyncio.sleep(0.02)
        self._record("approval_unanswered_detected", employee_id)
        # Resume before escalating: the SPEC §12 state machine allows
        # WAITING_FOR_HUMAN -> RUNNING -> BLOCKED, never a direct jump.
        resumed = await self.backend.post(
            f"/workflows/{workflow_id}/status",
            headers={"X-Agent-Id": _ACCESS_AGENT},
            json={"workflow_id": workflow_id, "status": "running", "blockers": []},
        )
        assert resumed.status_code == 200, resumed.text
        await self._escalate_blocked(
            employee_id,
            workflow_id,
            _ACCESS_AGENT,
            "APPROVAL_SLA_TIMEOUT",
            "manager approval unanswered within the human-task SLA; escalated "
            "per access escalation policy",
        )


def _assert_chaos_verdict_map(agent: ScriptedChaosAgent) -> None:
    """The readiness verdict per employee — the SPEC §20 question.

    Documented expected map: 9 READY / 3 NOT READY. The NOT READY outcomes are
    what the knowledge corpora mandate for unresolvable blockers:
    knowledge/access/escalation.md ("unresolvable access blockers are
    escalated through the workflow: report the workflow blocked ... with a
    machine-readable blocker code") and knowledge/devices/escalation.md
    (escalate to the onboarding coordinator). The scripted agents report
    BLOCKED; the coordinator's blocker reconciliation opens an ops-lead task
    that nobody answers in this scenario, so each missing goal settles with
    status "blocked" and its blocker code as the audit evidence.
    """
    assert set(agent.verdicts) == set(_CHAOS_EMPLOYEES)
    for employee_id in _CHAOS_READY:
        verdict = agent.verdicts[employee_id]
        assert verdict["verdict"] == "READY", verdict
        assert set(verdict["ready_goals"]) == _READY_GOALS
        assert verdict["missing_goals"] == []
    for employee_id, (goal, code) in _CHAOS_NOT_READY.items():
        verdict = agent.verdicts[employee_id]
        assert verdict["verdict"] == "NOT_READY", verdict
        assert len(verdict["missing_goals"]) == 1, verdict
        missing = verdict["missing_goals"][0]
        assert missing["goal"] == goal, verdict
        assert missing["status"] == "blocked", verdict
        assert any(b.get("code") == code for b in missing["blockers"]), verdict


async def _run_chaos_and_assert_pass(
    world_app: FastAPI,
    backend_client: httpx.AsyncClient,
    coordinator: CoordinatorAgent,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Any,
) -> tuple[ScriptedChaosAgent, dict[str, Any], Any]:
    """Run the chaos scenario through the real engines and assert it scores PASS.

    Shared by the parametrized hidden runner (every hidden YAML must pass) and
    the dedicated chaos verdict test (which adds the audit-trail and ordering
    assertions on top of the returned artifacts).
    """
    assert scenario.id == _CHAOS_SCENARIO_ID, scenario.id
    monkeypatch.setattr(coordinator_module, "POLL_INTERVAL_SECONDS", 0.02)
    # The chaos run deliberately leaves human tasks unanswered; shrink the
    # coordinator's HITL no-response SLA so its escalation path resolves in
    # test time (the DEC-08 default is 300s).
    monkeypatch.setattr(coordinator_module, "HITL_NO_RESPONSE_SECONDS", 0.3)
    coordinator.agent.before_model_callback = _canned_response

    holder: dict[str, ScriptedChaosAgent] = {}

    def factory(
        fault_callbacks: tuple[Any, Any],
    ) -> tuple[ScriptedChaosAgent, asyncio.Event]:
        agent = ScriptedChaosAgent(backend_client, coordinator, world_app, fault_callbacks)
        holder["agent"] = agent
        return agent, asyncio.Event()

    scenario_faults.clear_faults()
    healers = [
        # The lying reserve fires exactly once (E308's first attempt); the
        # request timeout fires exactly MAX_RETRIES times (E311's attempts).
        asyncio.create_task(_heal_fault_window("reserve_device", 1)),
        asyncio.create_task(_heal_fault_window("request_group_access", 3)),
    ]
    try:
        result = await ScenarioEngine().run(
            scenario,
            factory,
            {
                "transport": httpx.ASGITransport(app=world_app),
                "base_url": "http://mockworld",
                "time_scale": _TIME_SCALE,
            },
        )
    finally:
        for healer in healers:
            healer.cancel()
        await asyncio.gather(*healers, return_exceptions=True)
    for healer in healers:
        # Both fault windows must have provably fired and been closed.
        assert healer.done() and not healer.cancelled(), "a fault window never closed"
        assert healer.exception() is None

    agent = holder["agent"]
    final_state = await _read_chaos_world_state()
    # Multi-case run: the no-case-contamination assertion (single run_case_id)
    # does not apply, so case ids are intentionally not supplied.
    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state=final_state,
        expected_state=_expected_chaos_state(),
        retry_count=0,
        delegation_depth=1,  # coordinator -> domain agent
    )

    assert score.passed is True, f"{scenario.id} failed: {score.model_dump_json()}"
    assert score.total >= score.threshold
    assert result.final_state == "completed"
    assert result.final_state in scenario.expected.allowed_final_states
    for event in scenario.expected.forbidden_events:
        assert event not in agent.timeline_events
    _assert_chaos_verdict_map(agent)
    return agent, final_state, score


async def test_chaos_scenario_readiness_verdict_and_audit_trail(
    world_app: FastAPI,
    domain_transports: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    coordinator: CoordinatorAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chaos scenario's readiness-verdict AC (SPEC §20, Epic C batch 2b).

    After the engine run scores PASS, this test makes the scenario's AC
    explicit: the coordinator's verdict answers "Are Monday's new joiners
    ready?" per employee (9 READY / 3 NOT READY), every NOT READY employee's
    case timeline carries its specific blocker evidence through the REAL
    backend routes (the BLOCKER_CREATED escalation event and the pending
    HumanTask rows), every READY employee has both OUTCOME_VERIFIED events,
    and the forbidden events are absent.

    Skips on hosts without the private archive (fresh clone / CI).
    """
    del domain_transports  # monkeypatched; used implicitly by the tools
    chaos_path = next(
        (path for path in _HIDDEN_FILES if path.stem == _CHAOS_SCENARIO_STEM), None
    )
    if chaos_path is None:
        pytest.skip("no chaos scenario on this host (fresh clone/CI)")
    scenario = load_scenario(chaos_path)
    agent, final_state, _score = await _run_chaos_and_assert_pass(
        world_app, backend_client, coordinator, monkeypatch, scenario
    )

    # --- audit-trail completeness for the NOT READY employees ----------------
    for employee_id, (goal, code) in _CHAOS_NOT_READY.items():
        del goal  # the goal mapping is asserted in _assert_chaos_verdict_map
        case_id = _CHAOS_CASES[employee_id]
        events = (await backend_client.get(f"/cases/{case_id}/events")).json()["events"]
        # The escalation event: BLOCKER_CREATED with the machine-readable code.
        blockers = [e for e in events if e["type"] == "BLOCKER_CREATED"]
        assert any(e["payload"].get("code") == code for e in blockers), (
            f"{employee_id}: no BLOCKER_CREATED with code {code} on the case timeline"
        )
        # The coordinator's escalation HITL task (ops-lead) is on the trail,
        # still open — nobody answered in this scenario.
        case_tasks = (
            await backend_client.get("/tasks", params={"case_id": case_id})
        ).json()
        assert any(
            task["requested_by"] == "onboarding-agent"
            and task["requested_from"] == "ops-lead"
            and task["status"] == "open"
            for task in case_tasks
        ), f"{employee_id}: the coordinator's escalation task is missing"

    # E310's unanswered approval: the pending HumanTask row is the evidence.
    e310_tasks = (
        await backend_client.get(
            "/tasks", params={"case_id": _CHAOS_CASES[_CHAOS_UNANSWERED_EMPLOYEE]}
        )
    ).json()
    assert any(
        task["requested_by"] == _ACCESS_AGENT
        and task["requested_from"] == _MANAGER_ID
        and task["status"] == "open"
        for task in e310_tasks
    ), "E310: the unanswered manager approval must remain a pending HumanTask row"

    # --- every READY employee's outcomes were verified ------------------------
    for employee_id in _CHAOS_READY:
        case_id = _CHAOS_CASES[employee_id]
        events = (await backend_client.get(f"/cases/{case_id}/events")).json()["events"]
        verified = [e for e in events if e["type"] == "OUTCOME_VERIFIED"]
        assert len(verified) == 2, f"{employee_id}: device + access OUTCOME_VERIFIED"

    # --- forbidden events absent (the engine scores this; the AC is explicit) --
    for event in scenario.expected.forbidden_events:
        assert event not in agent.timeline_events

    # --- fault-mode-specific evidence ------------------------------------------
    # E308: the lie was detected before the successful reserve, and the fake
    # success shape's device id never reached the world.
    e308_events = [e for emp, e in agent.detail_events if emp == _CHAOS_LYING_EMPLOYEE]
    assert e308_events.index("provisioning_lie_detected") < e308_events.index(
        "device_reserved"
    )
    e308_assignment = await device.get_device_assignment(_CHAOS_LYING_EMPLOYEE)
    assert e308_assignment["assigned_device"]["id"] != _FAKE_DEVICE_ID
    assert final_state[f"{_CHAOS_LYING_EMPLOYEE}_device"] == "assigned"

    # E306: the manager flip was observed before the approval flow; the OLD
    # manager's attempt was rejected before the NEW manager's approval, and the
    # privileged world request went out only after the approval.
    e306_events = [
        e for emp, e in agent.detail_events if emp == _CHAOS_MANAGER_CHANGE_EMPLOYEE
    ]
    assert (
        e306_events.index("manager_change_detected")
        < e306_events.index("unauthorized_approval_rejected")
        < e306_events.index("approval_granted")
        < e306_events.index(f"access_requested:{_PRIVILEGED_GROUP}")
    )
    e306_tasks = (
        await backend_client.get(
            "/tasks", params={"case_id": _CHAOS_CASES[_CHAOS_MANAGER_CHANGE_EMPLOYEE]}
        )
    ).json()
    assert any(
        task["requested_from"] == _CHAOS_NEW_MANAGER
        and task["resolved_by"] == _CHAOS_NEW_MANAGER
        and task["status"] == "resolved"
        for task in e306_tasks
    ), "E306: only the current manager may resolve the approval"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world_app),
        base_url="http://mockworld",
        headers={"X-Agent-Id": _ACCESS_AGENT},
    ) as world_client:
        e306_world = (await world_client.get("/world/employees/E306")).json()
    assert e306_world["manager_id"] == _CHAOS_NEW_MANAGER

    # E309: the knowledge conflict was detected before the (re-)request, and
    # the world ends with exactly one standard-group request — no duplicate.
    e309_events = [
        e for emp, e in agent.detail_events if emp == _CHAOS_CONFLICT_EMPLOYEE
    ]
    assert e309_events.index("knowledge_conflict_detected") < e309_events.index(
        f"access_requested:{_STANDARD_GROUP}"
    )
    e309_requests = (await access.list_access_requests(_CHAOS_CONFLICT_EMPLOYEE))[
        "requests"
    ]
    assert len(e309_requests) == 1
    assert e309_requests[0]["group_id"] == _STANDARD_GROUP
    assert e309_requests[0]["status"] == "granted"


# --- DEC-14 distribution guard (always runs, including CI) ---------------------

# Static needles: this batch's hidden scenario id and its placeholder employee
# ids, plus the hidden-dir path itself. Dynamic needles: every scenario id
# found in the host's hidden dir (so future hidden scenarios are guarded too).
_STATIC_HIDDEN_NEEDLES = (
    "hidden-01-unknown-exceptions",
    "scenarios/hidden",
    "E201",
    "E202",
    "E203",
)


def _hidden_scenario_ids() -> list[str]:
    """Scenario ids declared by whatever hidden YAMLs this host has."""
    ids: list[str] = []
    for path in _hidden_scenario_files():
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(raw, dict) and isinstance(raw.get("id"), str):
            ids.append(raw["id"])
    return ids


def _participant_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def _assert_no_hidden_references(root: Path) -> None:
    needles = [*_STATIC_HIDDEN_NEEDLES, *_hidden_scenario_ids()]
    for path in _participant_files(root):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path} references hidden scenario {needle!r}"


def test_gitignore_covers_hidden_scenarios() -> None:
    """The DEC-14 gitignore rule must survive every edit of .gitignore."""
    lines = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "scenarios/hidden/" in {line.strip() for line in lines}


def test_template_ships_no_scenarios_or_hidden_references() -> None:
    """The team-agent template ships zero YAML and no hidden references."""
    template = _REPO_ROOT / "templates" / "team-agent"
    assert template.is_dir()
    assert sorted(template.rglob("*.yaml")) == []
    assert sorted(template.rglob("*.yml")) == []
    _assert_no_hidden_references(template)


def test_knowledge_has_no_hidden_references() -> None:
    """Participant knowledge corpora never leak hidden scenario ids."""
    _assert_no_hidden_references(_REPO_ROOT / "knowledge")


def test_domain_packs_read_only_their_domain_dirs() -> None:
    """The device/access certification packs read ONLY their own domain
    scenario dirs — never scenarios/hidden/ and never the env override."""
    packs = {
        "agents/device/tests/test_certification_pack.py": '"scenarios" / "devices"',
        "agents/access/tests/test_certification_pack.py": '"scenarios" / "access"',
    }
    for relative, domain_dir in packs.items():
        source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
        assert domain_dir in source, f"{relative} lost its domain scenario dir"
        assert '"hidden"' not in source
        assert "scenarios/hidden" not in source
        assert _HIDDEN_DIR_ENV not in source
