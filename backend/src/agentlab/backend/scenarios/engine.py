"""Scenario engine (SPEC §16, DEC-01/DEC-09/PATTERNS §4).

The engine plays reality: it drives MockWorld over HTTP (nothing else) and an
agent under test. The agent's own backend interactions are its own business;
the engine never touches the backend app, the event store, or SQLite directly.

Flow (SPEC §8)::

    POST /simulation/reset
    POST /simulation/load   (scenario.initial_state, verbatim)
    build agent via agent_factory(fault_callbacks)
    schedule each event  (asyncio.sleep(at) then POST /simulation/mutate)
    schedule each fault  (arm the fault filter at `at`)
    run the agent to terminal state
    cancel timers, collect timeline + final workflow state

Timing can be scaled for deterministic tests via ``run_kwargs["time_scale"]``.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from agentlab.backend.scenarios import faults
from agentlab.backend.scenarios.models import Scenario, ScenarioEvent, ScenarioFault

DEFAULT_MOCKWORLD_URL = "http://localhost:8000"
_DEFAULT_USER_MESSAGE = "Run the onboarding workflow for this scenario."

# ``agent_factory`` returns ``(agent, terminate_signal)``. ``agent`` exposes
# ``async run(user_message: str) -> str`` plus, for the harness, optional
# ``timeline_events`` (observed domain events) and ``final_state`` attributes.
AgentFactory = Callable[[tuple[Callable[..., Any], Callable[..., Any]]], Any]


@dataclass
class ScenarioResult:
    """Deterministic outcome of a single scenario run (SPEC §16)."""

    scenario_id: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    final_state: str | None = None
    events: list[str] = field(default_factory=list)
    faults_applied: list[dict[str, Any]] = field(default_factory=list)


class ScenarioEngine:
    """Drive MockWorld mutations + tool faults while an agent runs."""

    def __init__(
        self,
        mockworld_url: str | None = None,
        simulation_token: str | None = None,
    ) -> None:
        self.mockworld_url = (
            mockworld_url or os.environ.get("MOCKWORLD_URL") or DEFAULT_MOCKWORLD_URL
        ).rstrip("/")
        self.simulation_token = simulation_token or os.environ.get("AGENTLAB_SIMULATION_TOKEN")

    # --- HTTP helpers ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        if self.simulation_token:
            return {"Authorization": f"Bearer {self.simulation_token}"}
        return {}

    async def _post(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await client.post(path, json=json, headers=self._headers())
        response.raise_for_status()
        return response.json()

    # --- timing helpers -------------------------------------------------------

    @staticmethod
    def _record(timeline: list[dict[str, Any]], kind: str, detail: dict[str, Any]) -> None:
        timeline.append({"ts": time.monotonic(), "kind": kind, "detail": detail})

    async def _schedule_mutate(
        self,
        client: httpx.AsyncClient,
        event: ScenarioEvent,
        timeline: list[dict[str, Any]],
        time_scale: float,
    ) -> None:
        await asyncio.sleep(event.at * time_scale)
        for path, value in event.mutate.items():
            await self._post(client, "/simulation/mutate", json={"path": path, "value": value})
        self._record(timeline, "mutate", {"at": event.at, "mutate": event.mutate})

    async def _schedule_fault(
        self,
        fault: ScenarioFault,
        timeline: list[dict[str, Any]],
        time_scale: float,
    ) -> None:
        await asyncio.sleep(fault.at * time_scale)
        faults.arm_fault(fault.tool, fault.kind)
        self._record(
            timeline,
            "fault_armed",
            {"at": fault.at, "tool": fault.tool, "kind": fault.kind},
        )

    # --- run -------------------------------------------------------------------

    async def run(
        self,
        scenario: Scenario,
        agent_factory: AgentFactory,
        run_kwargs: dict[str, Any] | None = None,
    ) -> ScenarioResult:
        """Reset + load the world, schedule mutations/faults, run the agent.

        Args:
            scenario: The validated scenario definition.
            agent_factory: ``(fault_callbacks) -> (agent, terminate_signal)``.
                ``agent`` must expose ``async run(user_message) -> str`` and may
                expose ``timeline_events`` / ``final_state`` for the harness.
            run_kwargs: Optional overrides: ``transport`` (httpx transport),
                ``base_url``, ``time_scale``, ``user_message``,
                ``tool_fake_shapes``.

        Returns:
            The collected :class:`ScenarioResult`.
        """
        run_kwargs = run_kwargs or {}
        time_scale = float(run_kwargs.get("time_scale", 1.0))
        base_url = run_kwargs.get("base_url") or self.mockworld_url
        transport = run_kwargs.get("transport")
        user_message = run_kwargs.get("user_message") or _DEFAULT_USER_MESSAGE
        tool_fake_shapes = run_kwargs.get("tool_fake_shapes")

        timeline: list[dict[str, Any]] = []
        faults.clear_faults()

        before_cb, after_cb = faults.build_fault_callbacks(scenario.faults, tool_fake_shapes)
        built = agent_factory((before_cb, after_cb))
        if inspect.isawaitable(built):
            built = await built
        agent, terminate_signal = built

        async with httpx.AsyncClient(base_url=base_url, transport=transport) as client:
            self._record(timeline, "reset", {})
            await self._post(client, "/simulation/reset")
            self._record(timeline, "load", {"state": scenario.initial_state})
            await self._post(client, "/simulation/load", json={"state": scenario.initial_state})

            event_tasks = [
                asyncio.create_task(self._schedule_mutate(client, ev, timeline, time_scale))
                for ev in scenario.events
            ]
            fault_tasks = [
                asyncio.create_task(self._schedule_fault(ft, timeline, time_scale))
                for ft in scenario.faults
            ]

            self._record(timeline, "agent_started", {"scenario_id": scenario.id})
            try:
                await self._run_agent(agent, user_message, terminate_signal)
            finally:
                for task in event_tasks + fault_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*event_tasks, *fault_tasks, return_exceptions=True)
            self._record(timeline, "agent_ended", {})

        return ScenarioResult(
            scenario_id=scenario.id,
            timeline=timeline,
            final_state=getattr(agent, "final_state", None),
            events=list(getattr(agent, "timeline_events", [])),
            faults_applied=faults.snapshot_applied_faults(),
        )

    @staticmethod
    async def _run_agent(
        agent: Any,
        user_message: str,
        terminate_signal: asyncio.Event | None,
    ) -> None:
        if terminate_signal is None:
            await agent.run(user_message)
            return

        run_task = asyncio.create_task(agent.run(user_message))
        stop_task = asyncio.create_task(terminate_signal.wait())
        try:
            done, _ = await asyncio.wait(
                {run_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if run_task in done:
                run_task.result()
            else:
                run_task.cancel()
        finally:
            for task in (run_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(run_task, stop_task, return_exceptions=True)
