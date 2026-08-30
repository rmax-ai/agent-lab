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
from agentlab.onboarding import CoordinatorAgent  # noqa: E402
from agentlab.world import db as world_db  # noqa: E402
from agentlab.world.models import Employee, Identity  # noqa: E402

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
