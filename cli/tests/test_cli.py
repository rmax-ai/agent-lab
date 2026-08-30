"""CLI tests (SPEC §26, A.14).

Covers: ``init`` scaffolding, ``dev --once`` startup checks against the
in-process lab, ``scenario run --scripted`` on the device happy path,
``trace`` on a case with events, and ``status`` reachability + registry.

Everything is deterministic: no live LLM, no external network — only the
loopback servers the CLI (or the test) boots itself.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from typer.testing import CliRunner

from agentlab.backend import db as backend_db
from agentlab.backend.app import create_app as create_backend_app
from agentlab.cli.main import app
from agentlab.world import db as world_db
from agentlab.world.app import create_app as create_world_app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_DIR = _REPO_ROOT / "templates" / "team-agent"
_HAPPY_PATH_SCENARIO = _REPO_ROOT / "scenarios" / "devices" / "01_happy_path.yaml"

runner = CliRunner()


def _free_port() -> int:
    """Grab an ephemeral loopback port for one test's servers."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def lab_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point both db modules at one temp SQLite file, like backend conftest."""
    db_path = tmp_path / "lab.db"
    monkeypatch.setenv("AGENTLAB_DB", str(db_path))
    monkeypatch.delenv("ALLOW_ANY_RESOLVER", raising=False)
    backend_db.reset_engine()
    world_db.reset_engine()
    yield db_path
    backend_db.reset_engine()
    world_db.reset_engine()


class _ServerThread:
    """Run one uvicorn server on a daemon thread (for sync CLI invocations)."""

    def __init__(self, fastapi_app: FastAPI, port: int) -> None:
        config = uvicorn.Config(
            fastapi_app, host="127.0.0.1", port=port, log_level="error"
        )
        self.server = uvicorn.Server(config)
        self.port = port
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _ServerThread:
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self.server.started:
                try:
                    response = httpx.get(f"{self.url}/openapi.json", timeout=1.0)
                    if response.status_code == 200:
                        return self
                except httpx.HTTPError:
                    pass
            time.sleep(0.05)
        raise RuntimeError(f"server on port {self.port} never became ready")

    def __exit__(self, *_exc: object) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=5.0)


def test_init_scaffolds_template_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """init copies the full template plus the canonical pyproject.toml."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "my-agent"])

    assert result.exit_code == 0, result.output
    project = tmp_path / "my-agent"
    for relative in (
        "agent.py",
        "instructions.md",
        "README.md",
        "knowledge/README.md",
        "tools/example.py",
        "pyproject.toml",
    ):
        assert (project / relative).is_file(), f"missing {relative}"

    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "team-agent"
    for package in ("agentlab-cli", "agentlab-sdk", "agentlab-backend", "agentlab-world"):
        assert package in pyproject["project"]["dependencies"]
        source = pyproject["tool"]["uv"]["sources"][package]
        assert source["git"] == "https://github.com/rmax-ai/agent-lab"
        assert source["subdirectory"]


def test_dev_once_prints_spec26_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lab_db: Path
) -> None:
    """dev --once boots both apps and prints the SPEC §26 checkmarks."""
    del lab_db  # env + engine reset happen in the fixture
    project = tmp_path / "team-agent"
    shutil.copytree(_TEMPLATE_DIR, project)
    monkeypatch.chdir(project)
    monkeypatch.delenv("AGENTLAB_AGENT_ID", raising=False)

    result = runner.invoke(
        app,
        ["dev", "--once", "--port", str(_free_port()), "--world-port", str(_free_port())],
    )

    assert result.exit_code == 0, result.output
    assert "✓ connected to Agent Lab" in result.output
    assert "✓ MockWorld available" in result.output
    # the template corpus is knowledge/README.md; the template ships no tools
    assert "✓ knowledge loaded: 1 documents" in result.output
    assert "✓ tools registered: 0" in result.output
    assert "✓ team-agent ONLINE" in result.output


def test_dev_once_fails_without_agent_py(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lab_db: Path
) -> None:
    """dev errors clearly when the cwd has no agent.py."""
    del lab_db
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["dev", "--once", "--port", str(_free_port()), "--world-port", str(_free_port())],
    )

    assert result.exit_code == 2
    assert "agent.py" in result.output


def test_scenario_run_scripted_happy_path_passes(lab_db: Path) -> None:
    """scenario run --scripted on the device happy path scores PASS."""
    del lab_db

    result = runner.invoke(
        app,
        [
            "scenario",
            "run",
            "--scenario",
            str(_HAPPY_PATH_SCENARIO),
            "--port",
            str(_free_port()),
            "--world-port",
            str(_free_port()),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "device-01-happy-path" in result.output
    assert "PASS" in result.output
    assert "passed: true" in result.output
    assert "score:" in result.output


def test_scenario_run_rejects_real_agent_mode(lab_db: Path) -> None:
    """--no-scripted must never make a live LLM call."""
    del lab_db

    result = runner.invoke(
        app,
        [
            "scenario",
            "run",
            "--scenario",
            str(_HAPPY_PATH_SCENARIO),
            "--no-scripted",
            "--port",
            str(_free_port()),
            "--world-port",
            str(_free_port()),
        ],
    )

    assert result.exit_code == 2
    assert "not implemented" in result.output


def test_trace_prints_case_timeline(lab_db: Path) -> None:
    """trace renders the chronological events for a case."""
    del lab_db
    port = _free_port()
    with _ServerThread(create_backend_app(), port) as backend:
        response = httpx.post(
            f"{backend.url}/cases",
            json={"case_id": "CASE-1", "employee_id": "E42", "context": {}},
        )
        assert response.status_code == 201, response.text
        response = httpx.post(
            f"{backend.url}/workflows",
            json={
                "workflow_id": "WF-CASE-1",
                "case_id": "CASE-1",
                "goal": "employee_device_ready",
                "employee_id": "E42",
                "context": {},
                "target_agent_id": "device-agent",
            },
            headers={"X-Agent-Id": "onboarding-coordinator"},
        )
        assert response.status_code == 201, response.text

        result = runner.invoke(
            app, ["trace", "--case", "CASE-1", "--backend-url", backend.url]
        )

    assert result.exit_code == 0, result.output
    assert "trace for case CASE-1" in result.output
    assert "WORKFLOW_DELEGATED" in result.output
    assert "onboarding-coordinator" in result.output
    assert "device-agent" in result.output  # payload summary names the target


def test_status_prints_reachability_and_agents(lab_db: Path) -> None:
    """status reports backend/MockWorld reachability plus the registry."""
    del lab_db
    backend_port, world_port = _free_port(), _free_port()
    with (
        _ServerThread(create_backend_app(), backend_port) as backend,
        _ServerThread(create_world_app(), world_port) as world,
    ):
        response = httpx.post(
            f"{backend.url}/agents/register",
            json={"agent_id": "device-agent", "tools": 6, "knowledge_docs": 3},
        )
        assert response.status_code == 201, response.text

        result = runner.invoke(
            app,
            ["status", "--backend-url", backend.url, "--world-url", world.url],
        )

    assert result.exit_code == 0, result.output
    assert f"backend:   reachable ({backend.url})" in result.output
    assert f"mockworld: reachable ({world.url})" in result.output
    assert "device-agent ONLINE" in result.output


def test_status_reports_unreachable(lab_db: Path) -> None:
    """status exits non-zero when nothing is listening."""
    del lab_db
    dead_backend = f"http://127.0.0.1:{_free_port()}"
    dead_world = f"http://127.0.0.1:{_free_port()}"

    result = runner.invoke(
        app, ["status", "--backend-url", dead_backend, "--world-url", dead_world]
    )

    assert result.exit_code == 1
    assert "unreachable" in result.output
