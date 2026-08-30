"""Scripted (deterministic, no-LLM) device-agent trajectories for scenario runs.

Mirrors the ScriptedPackAgent pattern from the device certification pack
(agents/device/tests/test_certification_pack.py): a canned trajectory per
scenario id that exercises the REAL MockWorld routes and the REAL backend
case/workflow APIs, recording the snake_case trajectory events the scenario
``expected`` block asserts on. The ScenarioEngine harness reads back
``timeline_events`` / ``final_state``.
"""

from __future__ import annotations

from typing import Any

import httpx

DEVICE_AGENT_ID = "device-agent"
_COORDINATOR_ID = "onboarding-coordinator"


class ScriptedTrajectoryError(Exception):
    """No scripted trajectory exists for the requested scenario/agent."""


class ScriptedDeviceAgent:
    """Canned device-agent trajectory, one per certification scenario id."""

    def __init__(self, scenario_id: str, backend_url: str, world_url: str) -> None:
        self.scenario_id = scenario_id
        self.backend_url = backend_url
        self.world_url = world_url
        self.employee_id = "E42"
        self.case_id = "ONB-E42"
        self.workflow_id = f"WF-{scenario_id}"
        self.timeline_events: list[str] = []
        self.final_state: str | None = None
        self.case_ids: list[str] = []
        self.reserved_skus: list[str] = []

    def _record(self, event: str) -> None:
        self.timeline_events.append(event)

    async def run(self, user_message: str) -> str:
        """Entry point used by the ScenarioEngine."""
        del user_message
        async with httpx.AsyncClient(base_url=self.backend_url) as backend:
            await self._open_workflow(backend)
            driver = self._drivers().get(self.scenario_id)
            if driver is None:
                raise ScriptedTrajectoryError(
                    f"no scripted trajectory for scenario {self.scenario_id!r}"
                )
            await driver(backend)
        self.case_ids = [self.case_id] * len(self.timeline_events)
        return "done"

    def _drivers(self) -> dict[str, Any]:
        return {"device-01-happy-path": self._drive_happy_path}

    # --- backend contract helpers -------------------------------------------

    async def _open_workflow(self, backend: httpx.AsyncClient) -> None:
        """Create the case, accept the WorkflowRequest, and ack ownership."""
        response = await backend.post(
            "/cases",
            json={"case_id": self.case_id, "employee_id": self.employee_id, "context": {}},
        )
        response.raise_for_status()
        response = await backend.post(
            "/workflows",
            json={
                "workflow_id": self.workflow_id,
                "case_id": self.case_id,
                "goal": "employee_device_ready",
                "employee_id": self.employee_id,
                "context": {},
                "target_agent_id": DEVICE_AGENT_ID,
            },
            headers={"X-Agent-Id": _COORDINATOR_ID},
        )
        response.raise_for_status()
        response = await backend.post(
            f"/workflows/{self.workflow_id}/ack",
            headers={"X-Agent-Id": DEVICE_AGENT_ID},
        )
        response.raise_for_status()

    async def _complete_verified(self, backend: httpx.AsyncClient) -> None:
        response = await backend.post(
            f"/workflows/{self.workflow_id}/complete",
            json={"verified": True},
            headers={"X-Agent-Id": DEVICE_AGENT_ID},
        )
        response.raise_for_status()
        self._record("outcome_verified")
        self.final_state = "completed"

    # --- world helpers --------------------------------------------------------

    async def _world_get(self, path: str) -> Any:
        async with httpx.AsyncClient(
            base_url=self.world_url, headers={"X-Agent-Id": DEVICE_AGENT_ID}
        ) as client:
            response = await client.get(path)
        response.raise_for_status()
        return response.json()

    async def _world_post(self, path: str, body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(
            base_url=self.world_url, headers={"X-Agent-Id": DEVICE_AGENT_ID}
        ) as client:
            response = await client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    async def _inventory_available(self) -> dict[str, int]:
        rows = await self._world_get("/world/devices/inventory")
        return {row["sku"]: int(row["available"]) for row in rows}

    # --- trajectories -----------------------------------------------------------

    async def _drive_happy_path(self, backend: httpx.AsyncClient) -> None:
        """requirements → inventory → reserve standard SKU → verify → complete."""
        requirements = await self._world_get(f"/world/devices/{self.employee_id}")
        assert "error" not in requirements, requirements

        available = await self._inventory_available()
        self._record("inventory_checked")
        sku = "macbook_pro_14"
        assert available.get(sku, 0) >= 1, f"expected {sku} in stock: {available}"

        await self._world_post(f"/world/devices/{self.employee_id}/reserve", {"sku": sku})
        self.reserved_skus.append(sku)
        self._record("device_reserved")

        assignment = await self._world_get(f"/world/devices/{self.employee_id}")
        assert assignment.get("assigned_device") is not None, assignment
        self._record("delivery_verified")

        await self._complete_verified(backend)


def expected_inventory_state(
    initial_state: dict[str, Any], reserved_skus: list[str]
) -> dict[str, int]:
    """Derive the expected final per-SKU availability from the scenario seed.

    Every ``inventory.<sku>.available`` key in ``initial_state`` contributes its
    seeded value, minus one per scripted reservation of that SKU.
    """
    expected: dict[str, int] = {}
    for key, value in initial_state.items():
        if key.startswith("inventory.") and key.endswith(".available"):
            sku = key.split(".")[1]
            expected[sku] = int(value)
    for sku in reserved_skus:
        expected[sku] = expected.get(sku, 0) - 1
    return expected
