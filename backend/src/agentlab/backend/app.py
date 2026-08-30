"""FastAPI app factory for the Agent Lab backend (SPEC §11/§12/§15/§23).

Owns the platform routes for cases, workflows, and human tasks. Every error is
the flat ``{"error": {"code", "description"}}`` envelope; malformed bodies are
rendered as ``422`` by FastAPI's validation handler.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import Depends, FastAPI, Header, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from agentlab.backend import cases, channels, db, events, hub, tasks, workflows
from agentlab.backend.errors import BackendError
from agentlab.backend.evaluation import scoring
from agentlab.backend.scenarios.loader import load_scenario
from agentlab.backend.scenarios.models import ScenarioConfigError
from agentlab.backend.statemachine import IllegalTransitionError
from agentlab.sdk.events import EventType
from agentlab.sdk.protocols import HumanTask, WorkflowRequest, WorkflowStatus

logger = logging.getLogger(__name__)

# app.py lives at backend/src/agentlab/backend/app.py, so parents[4] is the
# repo root (same resolution pattern as mock-world db.py).
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Scenario pack root. Module-level so tests can point the platform routes at a
# tmp tree. AGENTLAB_HIDDEN_DIR is deliberately NOT honoured here: that env var
# belongs to the hidden-scenario test runner, not the API surface.
SCENARIOS_ROOT = _REPO_ROOT / "scenarios"

# Team-pack domains, in listing order. "hidden" is handled separately.
_SCENARIO_DOMAINS: tuple[str, ...] = ("devices", "access", "integration")


def _require_agent_id(
    x_agent_id: Annotated[str | None, Header(alias="X-Agent-Id")] = None,
) -> str:
    """Require an ``X-Agent-Id`` header on agent-facing workflow calls."""
    agent_id = (x_agent_id or "").strip()
    if not agent_id:
        raise BackendError(401, "UNAUTHORIZED", "Missing X-Agent-Id header")
    return agent_id


class CaseCreate(BaseModel):
    """Request body for ``POST /cases``."""

    case_id: str
    employee_id: str
    context: dict[str, Any]


class WorkflowCreate(WorkflowRequest):
    """Request body for ``POST /workflows``: a WorkflowRequest plus the target."""

    target_agent_id: str


class CompleteRequest(BaseModel):
    """Request body for ``POST /workflows/{id}/complete``."""

    verified: bool


class FailRequest(BaseModel):
    """Request body for ``POST /workflows/{id}/fail``."""

    reason: str


class DecisionRequest(BaseModel):
    """Request body for ``POST /tasks/{id}/decision``."""

    decision: dict[str, Any]
    resolved_by: str


class AgentRegister(BaseModel):
    """Request body for ``POST /agents/register`` (HTTP fallback registration)."""

    agent_id: str
    tools: int = 0
    knowledge_docs: int = 0


class EventCreate(BaseModel):
    """Request body for ``POST /events`` (SPEC §23).

    ``actor`` is always forced to the ``X-Agent-Id`` header value and ``ts`` is
    server-set; client-supplied values for either are ignored. ``type`` is
    validated against the EventType vocabulary (SPEC §23).
    """

    case_id: str
    workflow_id: str | None = None
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


SessionDep = Annotated[Session, Depends(db.get_session)]
AgentIdDep = Annotated[str, Depends(_require_agent_id)]


def _describe(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        message = str(error.get("msg", "invalid"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "Invalid request body"


def _parse_since(since: str | None) -> datetime | None:
    """Parse the ``since`` query parameter into an aware-or-naive datetime."""
    if not since:
        return None
    try:
        return datetime.fromisoformat(since)
    except ValueError as exc:
        raise BackendError(400, "INVALID_SINCE", f"Invalid since timestamp {since!r}") from exc


def _read_yaml_mapping(path: Path) -> dict[str, Any] | None:
    """Best-effort YAML mapping read; ``None`` (logged) on any failure."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("skipping unreadable scenario file %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("skipping non-mapping scenario file %s", path)
        return None
    return raw


def _display_path(path: Path) -> str:
    """Repo-relative path when possible, else the bare filename."""
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _team_scenario_entry(path: Path, domain: str) -> dict[str, Any] | None:
    """Build the listing entry for one team-pack YAML, or ``None`` to skip it.

    The expected block comes from the validated :class:`Scenario` model. A
    ``description`` is taken from the raw YAML when present (the schema
    currently forbids extra keys, so today it never is) and otherwise falls
    back to the scenario id. Invalid files are logged and skipped, never fatal.
    """
    try:
        scenario = load_scenario(path)
    except ScenarioConfigError as exc:
        logger.warning("skipping invalid scenario file %s: %s", path, exc)
        return None
    raw = _read_yaml_mapping(path) or {}
    description = raw.get("description")
    if isinstance(description, str) and description.strip():
        description = description.strip().splitlines()[0]
    else:
        description = scenario.id
    return {
        "id": scenario.id,
        "domain": domain,
        "file": _display_path(path),
        "description": description,
        "required_events": list(scenario.expected.required_events),
        "allowed_final_states": list(scenario.expected.allowed_final_states),
        "forbidden_events": list(scenario.expected.forbidden_events),
    }


def _hidden_scenario_entry(path: Path) -> dict[str, Any] | None:
    """Build the DEC-14-minimal hidden entry: id + file + domain + flag only.

    Hidden scenarios are never shipped to participants; this route serves the
    platform console, but even here the YAML contents (initial_state, events,
    expected) stay on disk — only list-level metadata is exposed.
    """
    raw = _read_yaml_mapping(path)
    if raw is None:
        return None
    scenario_id = raw.get("id")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        logger.warning("skipping hidden scenario file without an id: %s", path)
        return None
    return {
        "id": scenario_id.strip(),
        "domain": "hidden",
        "file": _display_path(path),
        "hidden": True,
    }


def _list_scenarios(root: Path) -> list[dict[str, Any]]:
    """Scan ``root`` for team packs plus (when present) the hidden directory."""
    entries: list[dict[str, Any]] = []
    for domain in _SCENARIO_DOMAINS:
        domain_dir = root / domain
        if not domain_dir.is_dir():
            continue
        for path in sorted(domain_dir.glob("*.yaml")):
            entry = _team_scenario_entry(path, domain)
            if entry is not None:
                entries.append(entry)
    hidden_dir = root / "hidden"
    if hidden_dir.is_dir():
        for path in sorted(hidden_dir.glob("*.yaml")):
            entry = _hidden_scenario_entry(path)
            if entry is not None:
                entries.append(entry)
    return entries


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BackendError)
    async def _backend_error_handler(_request: Request, exc: BackendError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "description": exc.description}},
        )

    @app.exception_handler(IllegalTransitionError)
    async def _illegal_transition_handler(
        _request: Request,
        exc: IllegalTransitionError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "ILLEGAL_TRANSITION", "description": str(exc)}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "description": _describe(exc)}},
        )


def create_app() -> FastAPI:
    """Build the backend app, initialising its owned tables."""
    app = FastAPI(title="Agent Lab Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)

    channel_hub = hub.ChannelHub()

    @app.post("/cases", status_code=201)
    def create_case(body: CaseCreate, session: SessionDep) -> dict[str, Any]:
        return cases.create_case(session, body.case_id, body.employee_id, body.context)

    @app.get("/cases")
    def list_cases(session: SessionDep) -> list[dict[str, Any]]:
        return cases.list_cases(session)

    @app.get("/cases/{case_id}")
    def get_case(case_id: str, session: SessionDep) -> dict[str, Any]:
        return cases.get_case(session, case_id)

    @app.get("/cases/{case_id}/events")
    def list_case_events(case_id: str, session: SessionDep) -> dict[str, Any]:
        return {"events": [event.to_dict() for event in events.list_events(session, case_id)]}

    @app.post("/events", status_code=201)
    def emit_agent_event(
        body: EventCreate,
        session: SessionDep,
        agent_id: AgentIdDep,
    ) -> dict[str, Any]:
        # The writer must be a REGISTERED agent (SPEC §14), and the event's
        # actor is forced to that identity so agents cannot spoof each other.
        if agent_id not in channel_hub.registry:
            raise BackendError(404, "NOT_FOUND", f"Agent {agent_id!r} is not registered")
        return events.record_agent_event(
            session,
            agent_id=agent_id,
            case_id=body.case_id,
            workflow_id=body.workflow_id,
            type=body.type,
            payload=body.payload,
        )

    @app.post("/workflows", status_code=201)
    def start_workflow(
        body: WorkflowCreate,
        session: SessionDep,
        delegator: AgentIdDep,
    ) -> dict[str, Any]:
        request = WorkflowRequest(
            workflow_id=body.workflow_id,
            case_id=body.case_id,
            goal=body.goal,
            employee_id=body.employee_id,
            context=body.context,
        )
        return workflows.start_workflow(
            session,
            request,
            body.target_agent_id,
            delegator=delegator,
        )

    @app.post("/workflows/{workflow_id}/ack")
    def acknowledge_workflow(
        workflow_id: str,
        session: SessionDep,
        agent_id: AgentIdDep,
    ) -> dict[str, Any]:
        return workflows.acknowledge(session, workflow_id, agent_id)

    @app.post("/workflows/{workflow_id}/status")
    def report_workflow_status(
        workflow_id: str,
        body: WorkflowStatus,
        session: SessionDep,
        agent_id: AgentIdDep,
    ) -> dict[str, Any]:
        return workflows.report_status(session, workflow_id, agent_id, body)

    @app.post("/workflows/{workflow_id}/complete")
    def complete_workflow(
        workflow_id: str,
        body: CompleteRequest,
        session: SessionDep,
        agent_id: AgentIdDep,
    ) -> dict[str, Any]:
        return workflows.complete_workflow(session, workflow_id, agent_id, body.verified)

    @app.post("/workflows/{workflow_id}/fail")
    def fail_workflow(
        workflow_id: str,
        body: FailRequest,
        session: SessionDep,
        agent_id: AgentIdDep,
    ) -> dict[str, Any]:
        return workflows.fail_workflow(session, workflow_id, agent_id, body.reason)

    @app.post("/tasks", status_code=201)
    def create_task(body: HumanTask, session: SessionDep) -> dict[str, Any]:
        return tasks.create_task(session, body)

    @app.get("/tasks")
    def list_tasks(
        session: SessionDep,
        case_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return tasks.list_tasks(session, case_id)

    @app.get("/tasks/{human_task_id}")
    def get_task(human_task_id: str, session: SessionDep) -> dict[str, Any]:
        return tasks.get_task(session, human_task_id)

    @app.post("/tasks/{human_task_id}/decision")
    def decide_task(
        human_task_id: str,
        body: DecisionRequest,
        session: SessionDep,
    ) -> dict[str, Any]:
        return tasks.decide(session, human_task_id, body.decision, body.resolved_by)

    @app.get("/agents")
    async def list_agents() -> dict[str, Any]:
        return {"agents": channel_hub.list_agents()}

    @app.post("/agents/register", status_code=201)
    async def register_agent_http(body: AgentRegister) -> dict[str, Any]:
        return channel_hub.register_agent(body.agent_id, body.tools, body.knowledge_docs)

    @app.get("/channels")
    def list_channels() -> dict[str, Any]:
        return {"channels": channels.list_channels()}

    @app.get("/channels/{channel}/messages")
    def list_channel_messages(
        channel: str,
        session: SessionDep,
        since: str | None = None,
    ) -> dict[str, Any]:
        since_dt = _parse_since(since)
        return {"messages": channels.get_history(session, channel, since_dt)}

    @app.get("/scenarios")
    def list_scenarios_route() -> dict[str, Any]:
        """List the scenario packs found on disk (SPEC §16/§28, DEC-14).

        Read-only, deterministic, no world or LLM dependency: the YAML files
        under ``scenarios/<domain>/`` are the single source of truth. Team
        packs (devices/access/integration) expose their full ``expected``
        block; hidden scenarios are listed only when ``scenarios/hidden/``
        exists on disk (it does not on fresh clones or CI — DEC-14) and even
        then only as minimal metadata (id, file, ``hidden: true``), never
        their YAML contents. Unparseable files are skipped and logged.

        Running a scenario over HTTP is deliberately not part of this route
        surface (deferred — see the Epic D production notes).
        """
        return {"scenarios": _list_scenarios(SCENARIOS_ROOT)}

    @app.get("/evals/model")
    def get_evaluation_model() -> dict[str, Any]:
        """The evaluation model inventory (SPEC §24).

        Everything is read from the real sources — the
        ``agentlab.backend.evaluation.scoring`` constants and the scenario
        YAML files on disk — so this inventory can never drift from the
        scorer. Evaluation-result persistence is deliberately not part of
        this route surface (deferred — see the Epic D production notes).
        """
        entries = _list_scenarios(SCENARIOS_ROOT)
        packs: dict[str, Any] = {
            domain: [
                entry["id"] for entry in entries if entry["domain"] == domain
            ]
            for domain in _SCENARIO_DOMAINS
        }
        hidden = [entry for entry in entries if entry["domain"] == "hidden"]
        if (SCENARIOS_ROOT / "hidden").is_dir():
            packs["hidden_count"] = len(hidden)
        return {
            "dimensions": [
                {"name": name, "weight": weight}
                for name, weight in scoring.CATEGORY_WEIGHTS.items()
            ],
            "threshold": scoring.PASS_THRESHOLD,
            # Mirrors score_scenario: total = sum(weight * score); passed when
            # total >= threshold.
            "pass_criterion": "sum(weight * score) >= threshold",
            "packs": packs,
        }

    @app.websocket("/ws/agents")
    async def agent_websocket(websocket: WebSocket) -> None:
        await channel_hub.handle_connection(websocket)

    db.create_all()
    return app
