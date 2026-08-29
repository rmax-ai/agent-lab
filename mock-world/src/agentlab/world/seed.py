"""Deterministic MockWorld seed (SPEC §9, DEC-02).

Runs at app startup and is idempotent: it seeds only when ``employees`` is
empty, so restarts and ``/simulation/reset`` land on the same canonical E42
scenario. Placeholder data only (E42, M1, example.test) — never real PII.
"""

from __future__ import annotations

from sqlmodel import Session, select

from agentlab.world.models import (
    WORLD_MODELS,
    Application,
    ApplicationAccess,
    Employee,
    Entitlement,
    Group,
    Identity,
    InventoryItem,
    Manager,
    System,
)


def seed(session: Session) -> None:
    """Insert the canonical world state unless employees already exist."""
    if session.exec(select(Employee).limit(1)).first() is not None:
        return

    session.add_all(
        [
            Manager(id="M1", name="Morgan Manager", email="m1@example.test"),
            Employee(
                id="E42",
                name="Eva Starter",
                role="Software Engineer",
                location="Amsterdam",
                manager_id="M1",
                start_date="2026-09-07",
                status="pending",
            ),
            InventoryItem(sku="macbook_pro_14", label="MacBook Pro 14", available=1),
            InventoryItem(sku="macbook_air_15", label="MacBook Air 15", available=7),
            Group(id="GRP-STANDARD", name="Standard", kind="baseline"),
            Group(id="GRP-PRIVILEGED", name="Privileged", kind="privileged"),
            Entitlement(
                id="ENT-E42-STANDARD",
                employee_id="E42",
                group_id="GRP-STANDARD",
                status="granted",
            ),
            Identity(employee_id="E42", username="eva.starter", status="created"),
            System(id="SYS-EMAIL", name="Email"),
            System(id="SYS-VPN", name="VPN"),
            System(id="SYS-HR", name="HR"),
            Application(id="APP-SLACK", name="Slack"),
            Application(id="APP-GOOGLE-WORKSPACE", name="Google Workspace"),
            Application(id="APP-GITHUB", name="GitHub"),
            ApplicationAccess(
                id="APPACC-E42-SLACK",
                employee_id="E42",
                application_id="APP-SLACK",
                status="granted",
            ),
            ApplicationAccess(
                id="APPACC-E42-WORKSPACE",
                employee_id="E42",
                application_id="APP-GOOGLE-WORKSPACE",
                status="granted",
            ),
        ]
    )
    session.commit()


def wipe(session: Session) -> None:
    """Delete every world-domain row (no FKs, so order is cosmetic)."""
    for model in reversed(WORLD_MODELS):
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.commit()


def reset_world(session: Session) -> None:
    """Wipe the world tables and reseed the canonical state (SPEC §17.1)."""
    wipe(session)
    seed(session)
