"""Schema-level tests for MockWorld (SPEC §9, DEC-02).

Assert ``create_all`` produces exactly the world-domain tables (and none of the
backend-owned case/workflow/task/event tables), the seed is idempotent, and WAL
mode is enabled.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import inspect, text
from sqlmodel import select

from agentlab.world import db
from agentlab.world.models import WORLD_MODELS, Employee
from agentlab.world.seed import seed


@pytest.fixture
def world_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    db_path = str(tmp_path / "world.db")
    monkeypatch.setenv("AGENTLAB_DB", db_path)
    db.reset_engine()
    return db_path


def test_create_all_produces_world_tables_only(world_db: str) -> None:
    db.create_all()
    tables = set(inspect(db.get_engine()).get_table_names())
    expected = {cast(str, model.__tablename__) for model in WORLD_MODELS}

    assert tables == expected
    assert len(expected) == 13
    assert tables.isdisjoint({"onboarding_cases", "workflow_runs", "human_tasks", "events"})


def test_seed_is_idempotent(world_db: str) -> None:
    db.create_all()

    with db.session_scope() as session:
        seed(session)
    with db.session_scope() as session:
        seed(session)

    with db.session_scope() as session:
        employees = session.exec(select(Employee)).all()
    assert len(employees) == 1
    assert employees[0].id == "E42"
    assert employees[0].name == "Eva Starter"


def test_seed_canonical_counts(world_db: str) -> None:
    db.create_all()

    with db.session_scope() as session:
        seed(session)

    with db.session_scope() as session:
        counts: dict[str, int] = {}
        for model in WORLD_MODELS:
            counts[cast(str, model.__tablename__)] = len(session.exec(select(model)).all())

    assert counts["employees"] == 1
    assert counts["managers"] == 1
    assert counts["inventory"] == 2
    assert counts["groups"] == 2
    assert counts["entitlements"] == 1
    assert counts["identities"] == 1
    assert counts["systems"] == 3
    assert counts["applications"] == 3
    assert counts["application_access"] == 2
    assert counts["devices"] == 0
    assert counts["device_orders"] == 0
    assert counts["access_requests"] == 0
    assert counts["system_accounts"] == 0


def test_wal_mode_on(world_db: str) -> None:
    db.create_all()
    with db.get_engine().connect() as connection:
        mode = connection.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"
