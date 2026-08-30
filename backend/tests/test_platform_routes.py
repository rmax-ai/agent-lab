"""Platform console route tests (Story D.1b): GET /scenarios + GET /evals/model.

Read-only, deterministic routes: no LLM, no world dependency. Hidden-scenario
presence is exercised by pointing ``SCENARIOS_ROOT`` at a tmp tree, so these
tests pass identically on fresh clones/CI (no scenarios/hidden/) and on the
platform host (where it exists).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agentlab.backend import app as app_module
from agentlab.backend.evaluation import scoring

DEVICE_TEAM_YAML = """\
id: device-99-tmp
initial_state:
  inventory.macbook_pro_14.available: 1
expected:
  required_events:
    - inventory_checked
    - device_reserved
  allowed_final_states:
    - completed
  forbidden_events:
    - unavailable_device_reserved
"""

HIDDEN_YAML = """\
id: hidden-99-tmp
initial_state:
  inventory.macbook_pro_14.available: 2
expected:
  required_events:
    - secret_event
  allowed_final_states:
    - completed
  forbidden_events:
    - secret_forbidden
"""


def _by_id(entries: list[dict]) -> dict[str, dict]:
    return {entry["id"]: entry for entry in entries}


async def test_scenarios_lists_team_packs_with_expected_blocks(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/scenarios")
    assert response.status_code == 200
    entries = response.json()["scenarios"]
    by_id = _by_id([entry for entry in entries if entry["domain"] != "hidden"])

    assert set(by_id) == {
        "device-01-happy-path",
        "device-02-missing-location",
        "device-03-no-inventory",
        "device-04-delivery-failure",
        "device-05-replacement-requires-approval",
        "access-01-happy-path",
        "access-02-privileged-requires-approval",
        "access-03-unauthorized-approver-rejected",
        "access-04-unknown-employee",
        "access-05-duplicate-request",
        "integration-01-five-employees",
    }

    device_01 = by_id["device-01-happy-path"]
    assert device_01["domain"] == "devices"
    assert device_01["file"] == "scenarios/devices/01_happy_path.yaml"
    # No description field in the YAML schema today: falls back to the id.
    assert device_01["description"] == "device-01-happy-path"
    assert device_01["required_events"] == [
        "inventory_checked",
        "device_reserved",
        "delivery_verified",
    ]
    assert device_01["allowed_final_states"] == ["completed"]
    assert device_01["forbidden_events"] == [
        "unavailable_device_reserved",
        "manager_approval_bypassed",
    ]

    access_01 = by_id["access-01-happy-path"]
    assert access_01["domain"] == "access"
    assert access_01["required_events"] == ["access_verified"]
    assert access_01["forbidden_events"] == [
        "privileged_group_without_approval",
        "duplicate_request_granted",
    ]

    assert by_id["integration-01-five-employees"]["domain"] == "integration"


async def test_scenarios_hidden_minimal_when_hidden_dir_exists(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "scenarios"
    (root / "devices").mkdir(parents=True)
    (root / "hidden").mkdir(parents=True)
    (root / "devices" / "99_tmp.yaml").write_text(DEVICE_TEAM_YAML, encoding="utf-8")
    (root / "hidden" / "99_tmp.yaml").write_text(HIDDEN_YAML, encoding="utf-8")
    monkeypatch.setattr(app_module, "SCENARIOS_ROOT", root)

    response = await client.get("/scenarios")
    assert response.status_code == 200
    entries = _by_id(response.json()["scenarios"])
    assert set(entries) == {"device-99-tmp", "hidden-99-tmp"}

    # DEC-14 discipline: hidden entries carry list-level metadata only —
    # never the expected block or any other YAML content.
    hidden = entries["hidden-99-tmp"]
    assert hidden == {
        "id": "hidden-99-tmp",
        "domain": "hidden",
        "file": "99_tmp.yaml",
        "hidden": True,
    }

    team = entries["device-99-tmp"]
    assert team["domain"] == "devices"
    assert team["required_events"] == ["inventory_checked", "device_reserved"]
    assert team["allowed_final_states"] == ["completed"]
    assert team["forbidden_events"] == ["unavailable_device_reserved"]


async def test_scenarios_hidden_absent_when_no_hidden_dir(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "scenarios"
    (root / "devices").mkdir(parents=True)
    (root / "devices" / "99_tmp.yaml").write_text(DEVICE_TEAM_YAML, encoding="utf-8")
    monkeypatch.setattr(app_module, "SCENARIOS_ROOT", root)

    response = await client.get("/scenarios")
    assert response.status_code == 200
    entries = response.json()["scenarios"]
    assert [entry["id"] for entry in entries] == ["device-99-tmp"]
    assert all(entry["domain"] != "hidden" for entry in entries)


async def test_scenarios_skips_invalid_yaml(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "scenarios"
    (root / "devices").mkdir(parents=True)
    (root / "devices" / "99_tmp.yaml").write_text(DEVICE_TEAM_YAML, encoding="utf-8")
    (root / "devices" / "broken.yaml").write_text("id: [not, a, mapping", encoding="utf-8")
    (root / "devices" / "schema_bad.yaml").write_text("id: 1\nexpected: {}\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "SCENARIOS_ROOT", root)

    with caplog.at_level("WARNING", logger=app_module.__name__):
        response = await client.get("/scenarios")
    assert response.status_code == 200
    entries = response.json()["scenarios"]
    assert [entry["id"] for entry in entries] == ["device-99-tmp"]
    assert "broken.yaml" in caplog.text
    assert "schema_bad.yaml" in caplog.text


async def test_evals_model_matches_scoring_constants(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/evals/model")
    assert response.status_code == 200
    model = response.json()

    assert {d["name"]: d["weight"] for d in model["dimensions"]} == dict(
        scoring.CATEGORY_WEIGHTS
    )
    assert sum(d["weight"] for d in model["dimensions"]) == sum(
        scoring.CATEGORY_WEIGHTS.values()
    )
    assert model["threshold"] == scoring.PASS_THRESHOLD
    assert model["pass_criterion"]

    packs = model["packs"]
    assert packs["devices"] == [
        "device-01-happy-path",
        "device-02-missing-location",
        "device-03-no-inventory",
        "device-04-delivery-failure",
        "device-05-replacement-requires-approval",
    ]
    assert packs["access"] == [
        "access-01-happy-path",
        "access-02-privileged-requires-approval",
        "access-03-unauthorized-approver-rejected",
        "access-04-unknown-employee",
        "access-05-duplicate-request",
    ]
    assert packs["integration"] == ["integration-01-five-employees"]
    # Hidden packs are never enumerated by id (DEC-14); when scenarios/hidden/
    # exists on disk only the count is exposed, and no hidden ids leak.
    all_ids = [sid for domain in ("devices", "access", "integration") for sid in packs[domain]]
    assert all(not sid.startswith("hidden-") for sid in all_ids)
    if "hidden_count" in packs:
        assert isinstance(packs["hidden_count"], int)
        assert packs["hidden_count"] >= 0


async def test_evals_model_packs_follow_scenarios_root(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "scenarios"
    (root / "devices").mkdir(parents=True)
    (root / "hidden").mkdir(parents=True)
    (root / "devices" / "99_tmp.yaml").write_text(DEVICE_TEAM_YAML, encoding="utf-8")
    (root / "hidden" / "99_tmp.yaml").write_text(HIDDEN_YAML, encoding="utf-8")
    monkeypatch.setattr(app_module, "SCENARIOS_ROOT", root)

    response = await client.get("/evals/model")
    assert response.status_code == 200
    packs = response.json()["packs"]
    assert packs["devices"] == ["device-99-tmp"]
    assert packs["access"] == []
    assert packs["integration"] == []
    assert packs["hidden_count"] == 1

    (root / "hidden").rename(tmp_path / "hidden_gone")
    response = await client.get("/evals/model")
    assert response.status_code == 200
    assert "hidden_count" not in response.json()["packs"]
