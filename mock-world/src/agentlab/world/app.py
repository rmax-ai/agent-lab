"""MockWorld FastAPI app (SPEC §8, DEC-07, DEC-09).

Two route families, enforced in code (never bypassable by agent tools):

* ``/world/*`` — agent-facing business APIs. Require ``X-Agent-Id`` and are
  domain-enforced from the first path segment; ``/world/employees/{id}`` is
  shared across registered agents.
* ``/simulation/*`` — privileged scenario-engine APIs. Require
  ``Authorization: Bearer <SIMULATION_TOKEN>``.

Blocks between agents and the shared SQLite state live here; SQLite is never
exposed to agents directly (SPEC §9).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from agentlab.world import db
from agentlab.world.models import (
    AccessRequest,
    Application,
    ApplicationAccess,
    Device,
    DeviceOrder,
    Employee,
    Entitlement,
    Group,
    Identity,
    InventoryItem,
    Manager,
    System,
    SystemAccount,
)
from agentlab.world.seed import reset_world
from agentlab.world.seed import seed as seed_world

# A device SKU every ENG employee requires (SPEC §7 standard device policy).
REQUIRED_SKU = "macbook_pro_14"
_NO_INVENTORY_DESCRIPTION = "Standard device unavailable"

# In-memory fault injection registry (SPEC §17.2); consumed by the scenario
# engine in a later story. Keyed by tool name.
ACTIVE_FAULTS: dict[str, dict[str, str]] = {}


class WorldApiError(Exception):
    """A domain error rendered as the flat ``{"error": {...}}`` envelope."""

    def __init__(self, status_code: int, code: str, description: str) -> None:
        super().__init__(description)
        self.status_code = status_code
        self.code = code
        self.description = description


# --- request body schemas (validated by FastAPI → 422 envelope) -----------------


class ReserveRequest(BaseModel):
    sku: str


class ReplaceRequest(BaseModel):
    reason: str


class AccessRequestCreate(BaseModel):
    group_id: str
    description: str | None = None


class ProvisionRequest(BaseModel):
    application_id: str


class MutateRequest(BaseModel):
    path: str
    value: Any


class LoadRequest(BaseModel):
    state: dict[str, Any]


class FaultRequest(BaseModel):
    tool: str
    fault: str


# --- auth + domain enforcement (DEC-07, DEC-09) ---------------------------------


def _allowed_domains() -> dict[str, set[str]]:
    """Parse ``ALLOWED_DOMAINS`` (``agent-id:domain`` pairs) into a lookup map."""
    raw = os.environ.get("ALLOWED_DOMAINS", "device-agent:devices")
    mapping: dict[str, set[str]] = {}
    for pair in raw.split(","):
        entry = pair.strip()
        if not entry:
            continue
        agent_id, _, domain = entry.partition(":")
        agent_id = agent_id.strip()
        domain = domain.strip()
        if agent_id and domain:
            mapping.setdefault(agent_id, set()).add(domain)
        elif agent_id:
            mapping.setdefault(agent_id, set())
    return mapping


def _simulation_token() -> str:
    """Return the bearer token protecting ``/simulation/*`` (DEC-09)."""
    return os.environ.get("SIMULATION_TOKEN", "dev-token")


def require_agent_id(
    x_agent_id: Annotated[str | None, Header(alias="X-Agent-Id")] = None,
) -> str:
    """Require an ``X-Agent-Id`` header on every agent-facing call."""
    agent_id = (x_agent_id or "").strip()
    if not agent_id:
        raise WorldApiError(401, "UNAUTHORIZED", "Missing X-Agent-Id header")
    return agent_id


def require_registered(
    agent_id: Annotated[str, Depends(require_agent_id)],
) -> str:
    """Require the caller to be a registered agent (shared ``/world/employees``)."""
    if agent_id not in _allowed_domains():
        raise WorldApiError(403, "FORBIDDEN", f"Unknown agent {agent_id!r}")
    return agent_id


def require_domain(domain: str) -> Callable[[str], str]:
    """Build a dependency enforcing that ``agent_id`` may call ``domain`` routes."""

    def _enforce(agent_id: Annotated[str, Depends(require_agent_id)]) -> str:
        if domain not in _allowed_domains().get(agent_id, set()):
            raise WorldApiError(
                403,
                "FORBIDDEN",
                f"Agent {agent_id!r} is not permitted in domain {domain!r}",
            )
        return agent_id

    return _enforce


def require_simulation_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Require ``Authorization: Bearer <SIMULATION_TOKEN>`` (DEC-09)."""
    expected = _simulation_token()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or token != expected:
        raise WorldApiError(401, "UNAUTHORIZED", "Missing or invalid simulation token")


# --- sync world operations (all called via asyncio.to_thread) -------------------


def _next_id(session: Session, model: type[Any], prefix: str) -> str:
    count = len(session.exec(select(model)).all())
    return f"{prefix}-{count + 1}"


def _get_employee(session: Session, employee_id: str) -> dict[str, Any]:
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise WorldApiError(404, "NOT_FOUND", f"Employee {employee_id!r} not found")
    manager = session.get(Manager, employee.manager_id) if employee.manager_id else None
    data = employee.model_dump()
    data["manager_name"] = manager.name if manager else None
    return data


def _list_inventory(session: Session) -> list[dict[str, Any]]:
    rows = session.exec(select(InventoryItem).order_by(InventoryItem.sku)).all()
    return [row.model_dump() for row in rows]


def _get_device_summary(session: Session, employee_id: str) -> dict[str, Any]:
    device = session.exec(
        select(Device).where(Device.employee_id == employee_id)
    ).first()
    order = session.exec(
        select(DeviceOrder).where(DeviceOrder.employee_id == employee_id)
    ).first()
    return {
        "required_sku": REQUIRED_SKU,
        "assigned_device": device.model_dump() if device else None,
        "order": order.model_dump() if order else None,
    }


def _reserve(session: Session, employee_id: str, sku: str) -> dict[str, Any]:
    inventory = session.exec(
        select(InventoryItem).where(InventoryItem.sku == sku)
    ).first()
    if inventory is None:
        raise WorldApiError(404, "NOT_FOUND", f"Unknown inventory sku {sku!r}")
    if inventory.available <= 0:
        raise WorldApiError(409, "NO_INVENTORY", _NO_INVENTORY_DESCRIPTION)
    inventory.available -= 1
    device = Device(
        id=_next_id(session, Device, "DEV"),
        employee_id=employee_id,
        sku=sku,
        status="assigned",
    )
    session.add(inventory)
    session.add(device)
    session.commit()
    return device.model_dump()


def _replace(session: Session, employee_id: str) -> dict[str, Any]:
    device = session.exec(
        select(Device).where(Device.employee_id == employee_id)
    ).first()
    if device is None:
        raise WorldApiError(404, "NOT_FOUND", f"No device assigned to {employee_id!r}")
    order = DeviceOrder(
        id=_next_id(session, DeviceOrder, "ORD"),
        employee_id=employee_id,
        sku=device.sku,
        status="ordered",
        eta=None,
    )
    device.status = "replacement_ordered"
    session.add(device)
    session.add(order)
    session.commit()
    return order.model_dump()


def _get_access_summary(session: Session, employee_id: str) -> dict[str, Any]:
    identity = session.get(Identity, employee_id)
    entitlements = session.exec(
        select(Entitlement).where(Entitlement.employee_id == employee_id)
    ).all()
    wanted_group_ids = {entitlement.group_id for entitlement in entitlements}
    groups = [
        group
        for group in session.exec(select(Group)).all()
        if group.id in wanted_group_ids
    ]
    return {
        "identity": identity.model_dump() if identity else None,
        "entitlements": [entitlement.model_dump() for entitlement in entitlements],
        "groups": [group.model_dump() for group in groups],
    }


def _request_access(
    session: Session,
    employee_id: str,
    group_id: str,
    description: str | None,
) -> dict[str, Any]:
    request = AccessRequest(
        id=_next_id(session, AccessRequest, "REQ"),
        employee_id=employee_id,
        group_id=group_id,
        description=description,
        status="requested",
    )
    session.add(request)
    session.commit()
    return request.model_dump()


def _list_access_requests(session: Session, employee_id: str) -> list[dict[str, Any]]:
    rows = session.exec(
        select(AccessRequest).where(AccessRequest.employee_id == employee_id)
    ).all()
    return [row.model_dump() for row in rows]


def _list_systems(session: Session, employee_id: str) -> list[dict[str, Any]]:
    systems = session.exec(select(System).order_by(System.id)).all()
    accounts = {
        account.system_id: account.status
        for account in session.exec(
            select(SystemAccount).where(SystemAccount.employee_id == employee_id)
        ).all()
    }
    return [
        {"system_id": system.id, "account_status": accounts.get(system.id, "missing")}
        for system in systems
    ]


def _list_applications(session: Session, employee_id: str) -> list[dict[str, Any]]:
    applications = session.exec(select(Application).order_by(Application.id)).all()
    grants = {
        access.application_id: access.status
        for access in session.exec(
            select(ApplicationAccess).where(ApplicationAccess.employee_id == employee_id)
        ).all()
    }
    return [
        {"application_id": application.id, "granted": grants.get(application.id) == "granted"}
        for application in applications
    ]


def _provision(
    session: Session,
    employee_id: str,
    application_id: str,
) -> dict[str, Any]:
    application = session.get(Application, application_id)
    if application is None:
        raise WorldApiError(404, "NOT_FOUND", f"Unknown application {application_id!r}")
    existing = session.exec(
        select(ApplicationAccess).where(
            ApplicationAccess.employee_id == employee_id,
            ApplicationAccess.application_id == application_id,
        )
    ).first()
    if existing is None:
        access = ApplicationAccess(
            id=_next_id(session, ApplicationAccess, "APPACC"),
            employee_id=employee_id,
            application_id=application_id,
            status="granted",
        )
        session.add(access)
        result: ApplicationAccess = access
    else:
        existing.status = "granted"
        session.add(existing)
        result = existing
    session.commit()
    return result.model_dump()


# --- dot-path mutation resolver (shared by /mutate and /load, SPEC §8) -----------


_MUTATE_TARGETS: dict[str, Any] = {
    "inventory": InventoryItem,
    "employees": Employee,
    "managers": Manager,
    "devices": Device,
    "device_orders": DeviceOrder,
    "entitlements": Entitlement,
    "identities": Identity,
    "access_requests": AccessRequest,
    "system_accounts": SystemAccount,
    "application_access": ApplicationAccess,
}


def apply_mutation(session: Session, path: str, value: Any) -> Any:
    """Set a single world field addressed by ``collection.<id>.<field>``."""
    parts = path.split(".")
    if len(parts) != 3:
        raise WorldApiError(404, "NOT_FOUND", f"Unknown path {path!r}")
    collection, key, field = parts

    model = _MUTATE_TARGETS.get(collection)
    if model is None:
        raise WorldApiError(404, "NOT_FOUND", f"Unknown path {path!r}")
    if field not in model.__table__.columns:
        raise WorldApiError(404, "NOT_FOUND", f"Unknown path {path!r}")

    row = session.get(model, key)
    if row is None:
        raise WorldApiError(404, "NOT_FOUND", f"Unknown path {path!r}")
    setattr(row, field, value)
    session.add(row)
    session.commit()
    return getattr(row, field)


def _load_state(session: Session, state: dict[str, Any]) -> None:
    reset_world(session)
    for path, value in state.items():
        apply_mutation(session, path, value)


# --- routers --------------------------------------------------------------------


employees_router = APIRouter(
    prefix="/world/employees",
    tags=["world"],
    dependencies=[Depends(require_registered)],
)
devices_router = APIRouter(
    prefix="/world/devices",
    tags=["world"],
    dependencies=[Depends(require_domain("devices"))],
)
access_router = APIRouter(
    prefix="/world/access",
    tags=["world"],
    dependencies=[Depends(require_domain("access"))],
)
systems_router = APIRouter(
    prefix="/world/systems",
    tags=["world"],
    dependencies=[Depends(require_domain("systems"))],
)
applications_router = APIRouter(
    prefix="/world/applications",
    tags=["world"],
    dependencies=[Depends(require_domain("applications"))],
)
simulation_router = APIRouter(
    prefix="/simulation",
    tags=["simulation"],
    dependencies=[Depends(require_simulation_token)],
)

SessionDep = Annotated[Session, Depends(db.get_session)]


@employees_router.get("/{employee_id}")
async def get_employee(employee_id: str, session: SessionDep) -> dict[str, Any]:
    """Return an employee plus their manager's name."""
    return await asyncio.to_thread(_get_employee, session, employee_id)


@devices_router.get("/inventory")
async def get_inventory(session: SessionDep) -> list[dict[str, Any]]:
    """List the current device inventory by SKU."""
    return await asyncio.to_thread(_list_inventory, session)


@devices_router.get("/{employee_id}")
async def get_device(employee_id: str, session: SessionDep) -> dict[str, Any]:
    """Return the required SKU plus the employee's assigned device and order."""
    return await asyncio.to_thread(_get_device_summary, session, employee_id)


@devices_router.post("/{employee_id}/reserve", status_code=201)
async def reserve_device(
    employee_id: str,
    body: ReserveRequest,
    session: SessionDep,
) -> dict[str, Any]:
    """Reserve a device SKU for an employee, decrementing inventory."""
    device = await asyncio.to_thread(_reserve, session, employee_id, body.sku)
    return {"device": device}


@devices_router.post("/{employee_id}/replace", status_code=201)
async def replace_device(
    employee_id: str,
    body: ReplaceRequest,
    session: SessionDep,
) -> dict[str, Any]:
    """Order a replacement for the employee's device and flip its status."""
    order = await asyncio.to_thread(_replace, session, employee_id)
    return {"order": order}


@access_router.get("/{employee_id}")
async def get_access(employee_id: str, session: SessionDep) -> dict[str, Any]:
    """Return identity, entitlements, and the groups behind them."""
    return await asyncio.to_thread(_get_access_summary, session, employee_id)


@access_router.post("/{employee_id}/request", status_code=201)
async def request_access(
    employee_id: str,
    body: AccessRequestCreate,
    session: SessionDep,
) -> dict[str, Any]:
    """Create an access request for a group."""
    request = await asyncio.to_thread(
        _request_access,
        session,
        employee_id,
        body.group_id,
        body.description,
    )
    return {"request": request}


@access_router.get("/{employee_id}/requests")
async def list_access_requests(
    employee_id: str,
    session: SessionDep,
) -> list[dict[str, Any]]:
    """List the employee's access requests."""
    return await asyncio.to_thread(_list_access_requests, session, employee_id)


@systems_router.get("/{employee_id}")
async def get_systems(employee_id: str, session: SessionDep) -> list[dict[str, Any]]:
    """List required systems and the employee's account status on each."""
    return await asyncio.to_thread(_list_systems, session, employee_id)


@applications_router.get("/{employee_id}")
async def get_applications(
    employee_id: str,
    session: SessionDep,
) -> list[dict[str, Any]]:
    """List applications and whether the employee has access."""
    return await asyncio.to_thread(_list_applications, session, employee_id)


@applications_router.post("/{employee_id}/provision", status_code=201)
async def provision_application(
    employee_id: str,
    body: ProvisionRequest,
    session: SessionDep,
) -> dict[str, Any]:
    """Grant (idempotently) an application to an employee."""
    access = await asyncio.to_thread(_provision, session, employee_id, body.application_id)
    return {"application_access": access}


# World mutation endpoints run their SQL in thread-pool threads against one
# SQLite file (process-global engine). Serialize mutating simulation calls so
# concurrent scenario runs can share one world instance without wipe/seed races.
_simulation_mutation_lock = asyncio.Lock()


@simulation_router.post("/reset")
async def reset(session: SessionDep) -> dict[str, str]:
    """Wipe the world tables and reseed the canonical state."""
    async with _simulation_mutation_lock:
        await asyncio.to_thread(reset_world, session)
    return {"status": "reset"}


@simulation_router.post("/load")
async def load(body: LoadRequest, session: SessionDep) -> dict[str, str]:
    """Reset, then apply a flat ``{dot.path: value}`` state."""
    async with _simulation_mutation_lock:
        await asyncio.to_thread(_load_state, session, body.state)
    return {"status": "loaded"}


@simulation_router.post("/mutate")
async def mutate(body: MutateRequest, session: SessionDep) -> dict[str, Any]:
    """Set a single world field via a dot-path."""
    async with _simulation_mutation_lock:
        value = await asyncio.to_thread(apply_mutation, session, body.path, body.value)
    return {"path": body.path, "value": value}


@simulation_router.post("/faults")
async def arm_fault(body: FaultRequest) -> dict[str, str]:
    """Arm an in-memory tool fault (SPEC §17.2)."""
    ACTIVE_FAULTS[body.tool] = {"tool": body.tool, "fault": body.fault}
    return {"status": "armed"}


@simulation_router.get("/faults")
async def list_faults() -> list[dict[str, str]]:
    """List currently armed tool faults."""
    return list(ACTIVE_FAULTS.values())


@simulation_router.post("/events")
async def events() -> JSONResponse:
    """Reject event injection: the backend event store owns this (story A.6)."""
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "code": "NOT_IMPLEMENTED",
                "description": "event injection lands with the backend event store (story A.6)",
            }
        },
    )


# --- app factory ----------------------------------------------------------------


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(WorldApiError)
    async def _world_api_error_handler(
        _request: Request,
        exc: WorldApiError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "description": exc.description}},
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


def _describe(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        message = str(error.get("msg", "invalid"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "Invalid request body"


def create_app() -> FastAPI:
    """Build the MockWorld app, initialising schema + canonical seed."""
    app = FastAPI(title="Agent Lab MockWorld", version="0.1.0")

    _register_exception_handlers(app)

    app.include_router(employees_router)
    app.include_router(devices_router)
    app.include_router(access_router)
    app.include_router(systems_router)
    app.include_router(applications_router)
    app.include_router(simulation_router)

    db.create_all()
    with db.session_scope() as session:
        seed_world(session)

    return app
