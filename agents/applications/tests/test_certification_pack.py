"""Applications certification pack runner (SPEC §18, Epic B replication #3).

Parametrized over ``scenarios/applications/01`` .. ``05``. Each scenario runs
a scripted applications agent — a deterministic canned trajectory per
scenario, no real LLM and no network beyond in-process ASGI — that drives
the REAL MockWorld applications tools (a full mutator surface: truthful read
PLUS an idempotent provisioning route) and the REAL backend
case/workflow/human-task APIs. The ScenarioEngine plays the world (reset →
load → DEC-05 fault arming); the EvaluationEngine scores the run against the
SPEC §24 weights. Every pack scenario must PASS (score ≥ threshold).

World-operator setup: unlike Systems, the applications domain needs no extra
rows — the canonical seed already carries the Application catalog and E42's
baseline grants, and the role changes (03/05) ride the ``initial_state``
load contract. :func:`_provision_world` mirrors the systems harness hook and
asserts that seed consistency holds before each run.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlmodel import select

from agentlab.backend.evaluation import EvaluationEngine
from agentlab.backend.evaluation.scoring import ScenarioScore
from agentlab.backend.scenarios import ScenarioEngine, load_scenario
from agentlab.backend.scenarios.engine import ScenarioResult
from agentlab.backend.scenarios.models import Scenario
from agentlab.sdk import MarkdownKnowledgeProvider
from agentlab.world import db as world_db
from agentlab.world.models import Application, ApplicationAccess

from ..agent import build_applications_agent
from ..tools import applications

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCENARIOS_DIR = _REPO_ROOT / "scenarios" / "applications"
_KNOWLEDGE_DIR = _REPO_ROOT / "knowledge" / "applications"

# The applications certification pack, in order.
PACK_SCENARIOS = [
    "01_happy_path.yaml",
    "02_missing_application.yaml",
    "03_wrong_role_mapping.yaml",
    "04_access_failure.yaml",
    "05_conflicting_policy.yaml",
]

_EMPLOYEE_ID = "E42"
_MANAGER_ID = "M1"
_IT_ACTOR = "it-support"  # the provisioning queue; only it may resolve (DEC-10)
_COORDINATOR_ID = "onboarding-coordinator"
_AGENT_ID = "applications-agent"
_GOAL = "employee_applications_ready"
_TIME_SCALE = 0.02  # the t=1 fault arms at ~0.02s in test time
_MAX_PROVISIONING_RETRIES = 3  # DEC-08 MAX_RETRIES (access-failure policy)

_UNKNOWN_APPLICATION_ID = "APP-UNKNOWN"  # absent from the world catalog

# Expected final world state, keyed by scenario id, read back through the real
# applications read tool after the run (per-application granted flag).
_EXPECTED_STATE: dict[str, dict[str, Any]] = {
    "applications-01-happy-path": {
        "app_slack": True,
        "app_google_workspace": True,
        "app_github": True,  # provisioned by the agent during the run
    },
    "applications-02-missing-application": {
        "app_slack": True,
        "app_google_workspace": True,
        "app_github": False,  # untouched: the unknown id blocked the run
    },
    "applications-03-wrong-role-mapping": {
        "app_slack": True,
        "app_google_workspace": True,
        "app_github": False,  # never provisioned for a non-engineering role
    },
    "applications-04-access-failure": {
        "app_slack": True,
        "app_google_workspace": True,
        "app_github": False,  # the fault was never recovered from
    },
    "applications-05-conflicting-policy": {
        "app_slack": True,
        "app_google_workspace": True,
        "app_github": False,  # untouched while the corpus conflict is open
    },
}


def _provision_world(scenario_id: str) -> None:
    """Assert the canonical applications seed the scenarios build on.

    Mirrors the systems harness's world-operator hook. The applications pack
    needs NO extra rows — the seed carries the catalog (APP-SLACK,
    APP-GOOGLE-WORKSPACE, APP-GITHUB) and E42's baseline grants — so this
    validates the contract instead of mutating it. Role changes (03/05) are
    applied by the engine through the ``initial_state`` load contract.
    """
    del scenario_id  # one canonical setup serves every pack scenario
    with world_db.session_scope() as session:
        catalog = {row.id for row in session.exec(select(Application)).all()}
        assert catalog == {"APP-SLACK", "APP-GOOGLE-WORKSPACE", "APP-GITHUB"}
        grants = {
            row.application_id: row.status
            for row in session.exec(select(ApplicationAccess)).all()
            if row.employee_id == _EMPLOYEE_ID
        }
        assert grants == {"APP-SLACK": "granted", "APP-GOOGLE-WORKSPACE": "granted"}


def _build_conflicting_corpus(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a TEMP corpus that contradicts the role-application mapping.

    A copy of ``knowledge/applications`` with ``provisioning-policy.md``
    modified to claim APP-GITHUB is required for EVERY employee — a direct
    contradiction of ``role-application-mapping.md`` (GitHub for engineering
    roles ONLY). The env override points the agent at the conflicted corpus.
    """
    conflicting_dir = Path(tempfile.mkdtemp(prefix="applications-conflict-"))
    for source in _KNOWLEDGE_DIR.glob("*.md"):
        (conflicting_dir / source.name).write_text(source.read_text(encoding="utf-8"))
    policy = conflicting_dir / "provisioning-policy.md"
    policy.write_text(
        policy.read_text(encoding="utf-8")
        + "\n\n## Addendum\n\nAPP-GITHUB is required for every employee, "
        "whatever their role — provision it for all roles.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTLAB_APPLICATIONS_KNOWLEDGE", str(conflicting_dir))
    return conflicting_dir


def _knowledge_conflicts() -> bool:
    """True when the loaded corpus contradicts the role-application mapping.

    Deterministic conflict detection over the Markdown corpus: the mapping
    document restricts APP-GITHUB to engineering roles; a conflict exists when
    any OTHER document claims APP-GITHUB for every employee. Guessing which
    document to follow is forbidden (policy-conflicts).
    """
    knowledge_dir = os.environ.get("AGENTLAB_APPLICATIONS_KNOWLEDGE") or str(
        _KNOWLEDGE_DIR
    )
    provider = MarkdownKnowledgeProvider(knowledge_dir)
    documents = {doc.id: doc.content.casefold() for doc in provider.documents}
    mapping_text = documents.get("role-application-mapping", "")
    mapping_engineering_only = "app-github" in mapping_text and "engineering" in mapping_text
    for doc_id, text in documents.items():
        if doc_id == "role-application-mapping":
            continue
        if "app-github" in text and "every employee" in text:
            return mapping_engineering_only
    return False


async def read_world_state(employee_id: str) -> dict[str, Any]:
    """Summarise world applications state via the agent's truthful read tool."""
    access = await applications.get_application_access(employee_id)
    grants = {
        row["application_id"]: bool(row["granted"])
        for row in access.get("applications", [])
        if isinstance(row, dict)
    }
    return {
        "app_slack": grants.get("APP-SLACK", False),
        "app_google_workspace": grants.get("APP-GOOGLE-WORKSPACE", False),
        "app_github": grants.get("APP-GITHUB", False),
    }


class ScriptedPackAgent:
    """A canned applications-agent trajectory per certification scenario.

    Records the snake_case trajectory events the scenario expects (see the
    vocabulary in ``scenarios/README.md``) while exercising the real
    applications tools and backend routes. Mutation-tool calls
    (``provision_application``) are routed through the engine-armed DEC-05
    fault callbacks, exactly as the ADK runtime would. ``final_state`` /
    ``timeline_events`` are the attributes the ScenarioEngine harness reads
    back.
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
        # The engine has already reset + loaded the world; validate the
        # canonical applications seed the scenario builds on.
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

    async def _call_provision_application(self, application_id: str) -> dict[str, Any]:
        """Invoke the mutation tool through the armed fault filter (ADK semantics).

        A ``timeout``/``http_500``/``stale`` fault raises before the world is
        ever reached; a ``success_without_state_change`` fault would
        short-circuit with a fake. Reads are never faulted (DEC-05).
        """
        args = {"employee_id": self.employee_id, "application_id": application_id}
        fake = await self._before_tool(
            SimpleNamespace(name="provision_application"), args, None
        )
        if fake is not None:
            return fake
        return await applications.provision_application(**args)

    async def _grants(self) -> dict[str, bool]:
        """Truthful read: per-application granted flag for the employee."""
        access = await applications.get_application_access(self.employee_id)
        return {
            row["application_id"]: bool(row["granted"])
            for row in access.get("applications", [])
            if isinstance(row, dict)
        }

    # --- per-scenario trajectories ---------------------------------------------

    async def _drive(self) -> None:
        drivers: dict[str, Callable[[], Any]] = {
            "applications-01-happy-path": self._drive_happy_path,
            "applications-02-missing-application": self._drive_missing_application,
            "applications-03-wrong-role-mapping": self._drive_wrong_role_mapping,
            "applications-04-access-failure": self._drive_access_failure,
            "applications-05-conflicting-policy": self._drive_conflicting_policy,
        }
        driver = drivers.get(self.scenario_id)
        assert driver is not None, f"no scripted trajectory for {self.scenario_id}"
        await driver()

    async def _drive_happy_path(self) -> None:
        """Baseline granted (seed); provision GitHub (engineer), verify, complete."""
        required = await applications.get_required_applications(self.employee_id)
        assert required["required_applications"] == [
            "APP-SLACK",
            "APP-GOOGLE-WORKSPACE",
            "APP-GITHUB",
        ]
        assert required["github_required"] is True  # E42 is an engineer
        grants = await self._grants()
        assert grants["APP-SLACK"] is True  # already granted: never re-provision
        assert grants["APP-GOOGLE-WORKSPACE"] is True
        assert grants["APP-GITHUB"] is False
        self._record("required_application_missing")
        result = await self._call_provision_application("APP-GITHUB")
        assert result["provisioned"] is True, result
        assert result["application_access"]["status"] == "granted"
        self._record("application_provisioned")
        verification = await applications.verify_application_access(self.employee_id)
        assert verification["all_required_verified"] is True, verification
        assert verification["policy_violations"] == []
        self._record("application_access_verified")
        await self._complete_verified()

    async def _drive_missing_application(self) -> None:
        """Unknown application id: 404 detected, escalated, never guessed."""
        result = await applications.provision_application(
            self.employee_id, _UNKNOWN_APPLICATION_ID
        )
        assert result["provisioned"] is False, result
        assert result["code"] == "NOT_FOUND"  # the honest 404 envelope
        self._record("unknown_application_detected")
        # unknown-applications policy: no retry, no guessed id — escalate.
        await self._create_task(
            "MISSING_INFORMATION",
            {
                "reason": "unknown_application_id",
                "application_id": _UNKNOWN_APPLICATION_ID,
                "employee_id": self.employee_id,
                "policy": "unknown-applications",
            },
            _MANAGER_ID,
        )
        await self._wait_for_human()
        self.final_state = "waiting_for_human"

    async def _drive_wrong_role_mapping(self) -> None:
        """Non-engineering role: baseline only; GitHub is never provisioned."""
        required = await applications.get_required_applications(self.employee_id)
        assert required["role"] == "Marketing Specialist"
        assert required["required_applications"] == ["APP-SLACK", "APP-GOOGLE-WORKSPACE"]
        assert required["github_required"] is False
        grants = await self._grants()
        assert grants["APP-SLACK"] is True
        assert grants["APP-GOOGLE-WORKSPACE"] is True
        assert grants["APP-GITHUB"] is False  # stays absent: out-of-role
        verification = await applications.verify_application_access(self.employee_id)
        assert verification["all_required_verified"] is True, verification
        assert verification["policy_violations"] == []
        self._record("application_access_verified")
        await self._complete_verified()

    async def _drive_access_failure(self) -> None:
        """provision_application faulted (http_500): bounded retries, escalate."""
        required = await applications.get_required_applications(self.employee_id)
        assert "APP-GITHUB" in required["required_applications"]
        grants = await self._grants()
        assert grants["APP-GITHUB"] is False
        self._record("required_application_missing")
        # Let the t=1 fault arm on the DEC-05 schedule before the first attempt.
        await asyncio.sleep(0.05)
        retries = 0
        while True:
            try:
                result = await self._call_provision_application("APP-GITHUB")
            except (TimeoutError, RuntimeError):
                result = {"provisioned": False, "code": "FAULT"}
            if result.get("provisioned"):
                break  # unreachable while the fault stays armed
            if retries >= _MAX_PROVISIONING_RETRIES:
                break  # access-failure policy: never retry forever
            retries += 1
            self._record("provisioning_retry")
        self.retry_count = retries
        assert retries == _MAX_PROVISIONING_RETRIES  # bounded, not unbounded
        self._record("provisioning_escalated")
        await self._create_task(
            "EXCEPTION_HANDLING",
            {
                "reason": "provisioning_unavailable",
                "application_id": "APP-GITHUB",
                "employee_id": self.employee_id,
                "policy": "access-failure-policy",
            },
            _IT_ACTOR,
        )
        await self._wait_for_human()
        self.final_state = "waiting_for_human"

    async def _drive_conflicting_policy(self) -> None:
        """Conflicted corpus: detect the contradiction, escalate, never guess."""
        required = await applications.get_required_applications(self.employee_id)
        assert required["role"] == "Marketing Specialist"  # GitHub NOT required
        assert required["github_required"] is False
        # The corpus claims APP-GITHUB for every employee AND (in the mapping)
        # for engineering roles only — a contradiction the agent must detect.
        assert _knowledge_conflicts() is True
        self._record("policy_conflict_detected")
        # policy-conflicts: STOP — no provisioning, no completion claim.
        await self._create_task(
            "CONFLICT_RESOLUTION",
            {
                "reason": "knowledge_corpus_conflict",
                "documents": ["role-application-mapping", "provisioning-policy"],
                "disagreement": "app-github-required-scope",
                "employee_id": self.employee_id,
                "policy": "policy-conflicts",
            },
            _MANAGER_ID,
        )
        await self._wait_for_human()
        self.final_state = "waiting_for_human"


async def run_pack_scenario(
    scenario_file: str,
    world_app: FastAPI,
    backend: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Scenario, ScenarioResult, ScenarioScore, ScriptedPackAgent]:
    """Run one pack scenario through the engines and return the score."""
    scenario = load_scenario(_SCENARIOS_DIR / scenario_file)
    if scenario.id == "applications-05-conflicting-policy":
        _build_conflicting_corpus(monkeypatch)
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
    applications_transport: httpx.ASGITransport,
    backend_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    scenario_file: str,
) -> None:
    """Every pack scenario scores PASS against the SPEC §24 threshold."""
    del applications_transport  # monkeypatched; implicit
    applications_agent = build_applications_agent()
    assert applications_agent.id == "applications-agent"

    (scenario, result, score, agent) = await run_pack_scenario(
        scenario_file, world_app, backend_client, monkeypatch
    )

    assert score.passed is True, f"{scenario.id} failed: {score.model_dump_json()}"
    assert score.total >= score.threshold
    assert result.final_state in scenario.expected.allowed_final_states
    for event in scenario.expected.forbidden_events:
        assert event not in agent.timeline_events

    if scenario.id == "applications-04-access-failure":
        # The retry cap is asserted via the trajectory: exactly MAX_RETRIES
        # bounded retries, then escalation — never an unbounded loop.
        retries = agent.timeline_events.count("provisioning_retry")
        assert retries == _MAX_PROVISIONING_RETRIES <= scenario.max_retries
        assert "provisioning_escalated" in agent.timeline_events
        # Every applied DEC-05 fault targeted the declared mutation tool.
        assert result.faults_applied
        assert all(
            fault["tool"] == "provision_application" for fault in result.faults_applied
        )
