"""``agent-lab scenario run`` — deterministic scenario execution + scoring.

Scripted mode (the default) runs a canned no-LLM trajectory through the real
in-process MockWorld and backend, driven by the ScenarioEngine and scored by
the EvaluationEngine (SPEC §16/§24/§25). No live LLM calls are ever made here.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from agentlab.backend.evaluation import EvaluationEngine
from agentlab.backend.evaluation.scoring import ScenarioScore
from agentlab.backend.scenarios import ScenarioEngine, ScenarioResult, load_scenario
from agentlab.backend.scenarios.models import Scenario, ScenarioConfigError
from agentlab.cli.scripted import (
    DEVICE_AGENT_ID,
    ScriptedDeviceAgent,
    ScriptedTrajectoryError,
    expected_inventory_state,
)
from agentlab.cli.servers import DEFAULT_BACKEND_PORT, DEFAULT_WORLD_PORT, start_lab

_TIME_SCALE = 0.02  # timed mutations land at 2% of their nominal t=
_SUMMARY_NAME_WIDTH = 34


async def _inventory_available(world_url: str) -> dict[str, int]:
    async with httpx.AsyncClient(
        base_url=world_url, headers={"X-Agent-Id": DEVICE_AGENT_ID}
    ) as client:
        response = await client.get("/world/devices/inventory")
    response.raise_for_status()
    return {row["sku"]: int(row["available"]) for row in response.json()}


async def _run_scripted(
    scenario: Scenario, backend_url: str, world_url: str
) -> tuple[ScenarioResult, ScenarioScore]:
    agent = ScriptedDeviceAgent(scenario.id, backend_url, world_url)

    def factory(fault_callbacks: tuple[Any, Any]) -> tuple[ScriptedDeviceAgent, None]:
        del fault_callbacks  # scripted trajectories declare no tool faults
        return agent, None

    engine = ScenarioEngine(
        mockworld_url=world_url,
        simulation_token=os.environ.get("SIMULATION_TOKEN", "dev-token"),
    )
    result = await engine.run(
        scenario,
        factory,
        {"base_url": world_url, "time_scale": _TIME_SCALE},
    )
    available = await _inventory_available(world_url)
    score = EvaluationEngine().evaluate(
        scenario,
        result,
        final_world_state=available,
        expected_state=expected_inventory_state(scenario.initial_state, agent.reserved_skus),
        case_ids=agent.case_ids,
        run_case_id=agent.case_id,
    )
    return result, score


def _print_result(
    scenario: Scenario, result: ScenarioResult, score: ScenarioScore, agent: str
) -> None:
    """Render the SPEC §25-style summary plus the per-category breakdown."""
    verdict = "PASS" if score.passed else "FAIL"
    typer.echo(f"{agent.upper()} AGENT")
    typer.echo(f"{'Scenario':<{_SUMMARY_NAME_WIDTH}}Result")
    typer.echo(f"{scenario.id:<{_SUMMARY_NAME_WIDTH}}{verdict}")
    typer.echo("")
    for category in score.categories:
        earned = category.weight * category.score
        typer.echo(f"{category.name:<35}{earned:>5.1f} / {category.weight:.1f}")
        for violation in category.violations:
            typer.echo(f"  ✗ {violation}")
    typer.echo(f"score: {score.total:.1f} / 100.0 (threshold {score.threshold:.1f})")
    typer.echo(f"passed: {str(score.passed).lower()}")
    if result.final_state is not None:
        typer.echo(f"final state: {result.final_state}")


async def _scenario_async(
    scenario_path: Path,
    agent: str,
    scripted: bool,
    backend_port: int,
    world_port: int,
) -> int:
    if agent != "device":
        typer.echo(f"error: unknown agent {agent!r} (only 'device' ships today)", err=True)
        return 2
    if not scripted:
        typer.echo(
            "error: real-agent mode is not implemented — no LLM is configured; "
            "use --scripted (the default)",
            err=True,
        )
        return 2
    try:
        scenario = load_scenario(scenario_path)
    except ScenarioConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        return 2

    lab = await start_lab(backend_port=backend_port, world_port=world_port)
    try:
        result, score = await _run_scripted(scenario, lab.backend_url, lab.world_url)
    except ScriptedTrajectoryError as exc:
        typer.echo(f"error: {exc}", err=True)
        return 2
    finally:
        await lab.stop()
    _print_result(scenario, result, score, agent)
    return 0 if score.passed else 1


def scenario_run_command(
    scenario: Annotated[Path, typer.Option("--scenario", help="Path to the scenario YAML.")],
    agent: Annotated[str, typer.Option("--agent", help="Agent under test.")] = "device",
    scripted: Annotated[
        bool, typer.Option("--scripted/--no-scripted", help="Deterministic no-LLM trajectory.")
    ] = True,
    port: Annotated[int, typer.Option("--port", help="Backend port.")] = DEFAULT_BACKEND_PORT,
    world_port: Annotated[
        int, typer.Option("--world-port", help="MockWorld port.")
    ] = DEFAULT_WORLD_PORT,
) -> None:
    """Run one scenario against the in-process lab and print the score."""
    exit_code = asyncio.run(_scenario_async(scenario, agent, scripted, port, world_port))
    if exit_code:
        raise typer.Exit(exit_code)
