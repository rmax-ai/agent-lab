"""World-domain SQLModel tables (SPEC §9).

Flat, snake_case rows with string business IDs (``"E42"``, ``"M1"``,
``"GRP-STANDARD"``). No foreign keys and no autoincrement: the single lab
database is shared with the backend, which adds case/workflow/task/event tables
in a later story. ``create_all`` must therefore stay scoped to
:data:`WORLD_MODELS`.
"""

from sqlmodel import Field, SQLModel


class Employee(SQLModel, table=True):
    """HR reality: a person being onboarded (SPEC §7)."""

    __tablename__ = "employees"

    id: str = Field(primary_key=True)
    name: str
    role: str
    location: str
    manager_id: str | None = None
    start_date: str
    status: str = "pending"


class Manager(SQLModel, table=True):
    """Approver identity for HITL (SPEC §15)."""

    __tablename__ = "managers"

    id: str = Field(primary_key=True)
    name: str
    email: str


class InventoryItem(SQLModel, table=True):
    """Device stock levels by SKU (SPEC §7)."""

    __tablename__ = "inventory"

    sku: str = Field(primary_key=True)
    label: str
    available: int = 0


class Device(SQLModel, table=True):
    """An assigned device instance (SPEC §10 device tools)."""

    __tablename__ = "devices"

    id: str = Field(primary_key=True)
    employee_id: str | None = None
    sku: str
    status: str = "unassigned"


class DeviceOrder(SQLModel, table=True):
    """Outstanding replacement/delivery order for a device (SPEC §17)."""

    __tablename__ = "device_orders"

    id: str = Field(primary_key=True)
    employee_id: str
    sku: str
    status: str = "ordered"
    eta: str | None = None


class Identity(SQLModel, table=True):
    """IAM identity state for an employee (SPEC §22)."""

    __tablename__ = "identities"

    employee_id: str = Field(primary_key=True)
    username: str
    status: str = "created"


class Group(SQLModel, table=True):
    """Access group catalog (baseline vs privileged)."""

    __tablename__ = "groups"

    id: str = Field(primary_key=True)
    name: str
    kind: str


class Entitlement(SQLModel, table=True):
    """An employee's grant of a group membership (SPEC §10 access tools)."""

    __tablename__ = "entitlements"

    id: str = Field(primary_key=True)
    employee_id: str
    group_id: str
    status: str = "pending"


class AccessRequest(SQLModel, table=True):
    """A request for an entitlement (may need HITL approval, SPEC §15)."""

    __tablename__ = "access_requests"

    id: str = Field(primary_key=True)
    employee_id: str
    group_id: str | None = None
    description: str | None = None
    status: str = "requested"


class System(SQLModel, table=True):
    """Required system catalog (SPEC §10 systems tools)."""

    __tablename__ = "systems"

    id: str = Field(primary_key=True)
    name: str


class SystemAccount(SQLModel, table=True):
    """Provisioned account for an employee on a system."""

    __tablename__ = "system_accounts"

    id: str = Field(primary_key=True)
    employee_id: str
    system_id: str
    status: str = "pending"


class Application(SQLModel, table=True):
    """Application catalog (SPEC §10 applications tools)."""

    __tablename__ = "applications"

    id: str = Field(primary_key=True)
    name: str


class ApplicationAccess(SQLModel, table=True):
    """Granted application access for an employee."""

    __tablename__ = "application_access"

    id: str = Field(primary_key=True)
    employee_id: str
    application_id: str
    status: str = "granted"


# The complete set of tables MockWorld owns. ``create_all`` and ``wipe`` iterate
# this list so backend-owned tables (cases, workflows, tasks, events) are never
# touched, even once they join the shared database in story A.6.
WORLD_MODELS: list[type[SQLModel]] = [
    Employee,
    Manager,
    InventoryItem,
    Device,
    DeviceOrder,
    Identity,
    Group,
    Entitlement,
    AccessRequest,
    System,
    SystemAccount,
    Application,
    ApplicationAccess,
]
