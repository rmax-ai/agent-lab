"""SQLite engine + session factory for the backend (SPEC §9, DEC-02).

The lab database is a single SQLite file shared with MockWorld. This module
owns only the backend tables (see :data:`agentlab.backend.models.BACKEND_MODELS`)
and never creates the world-domain tables. The path comes from ``AGENTLAB_DB``
(default ``./agent-lab.db`` relative to the repo root) and WAL mode is enabled
on connect.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, event
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from agentlab.backend import models

# db.py lives at backend/src/agentlab/backend/db.py, so parents[4] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_DB = _REPO_ROOT / "agent-lab.db"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _database_path() -> Path:
    """Resolve the database path from ``AGENTLAB_DB`` or the repo-root default."""
    configured = os.environ.get("AGENTLAB_DB")
    if configured:
        return Path(configured)
    return _DEFAULT_DB


def _build_engine() -> Engine:
    """Create a SQLite engine with WAL enabled and cross-thread access allowed."""
    engine = create_engine(
        f"sqlite:///{_database_path()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_wal(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def get_engine() -> Engine:
    """Return the process-wide engine, building it on first use."""
    global _engine, _session_factory
    if _engine is None:
        _engine = _build_engine()
        _session_factory = sessionmaker(
            bind=_engine,
            class_=Session,
            expire_on_commit=False,
        )
    return _engine


def _get_session_factory() -> sessionmaker[Session]:
    """Return the session factory, initialising the engine if needed."""
    get_engine()
    assert _session_factory is not None
    return _session_factory


def reset_engine() -> None:
    """Dispose the engine so the next access re-reads ``AGENTLAB_DB``.

    Used by tests to point the backend at a different database path.
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def create_all() -> None:
    """Create the backend-owned tables, scoped strictly to ``BACKEND_MODELS``."""
    tables = [cast(Any, model).__table__ for model in models.BACKEND_MODELS]
    SQLModel.metadata.create_all(get_engine(), tables=tables)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a scoped SQLModel session for one-shot (startup/test) work."""
    with _get_session_factory()() as session:
        yield session


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped SQLModel session per request."""
    with _get_session_factory()() as session:
        yield session
