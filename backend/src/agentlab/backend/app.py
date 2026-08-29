"""FastAPI app factory for the Agent Lab backend (SPEC §11/§12/§15/§23).

Owns the platform routes for cases, workflows, and human tasks. Every error is
the flat ``{"error": {"code", "description"}}`` envelope; malformed bodies are
rendered as ``422`` by FastAPI's validation handler.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session

from agentlab.backend import cases, db, events, tasks, workflows
from agentlab.backend.errors import BackendError
from agentlab.backend.statemachine import IllegalTransitionError
from agentlab.sdk.protocols import HumanTask, WorkflowRequest, WorkflowStatus


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


SessionDep = Annotated[Session, Depends(db.get_session)]
AgentIdDep = Annotated[str, Depends(_require_agent_id)]


def _describe(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        message = str(error.get("msg", "invalid"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "Invalid request body"


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
    """Build the backend app, initialising its four owned tables."""
    app = FastAPI(title="Agent Lab Backend", version="0.1.0")

    _register_exception_handlers(app)

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

    db.create_all()
    return app
