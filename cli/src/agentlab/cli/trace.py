"""``agent-lab trace`` — print a case's event timeline (SPEC §23)."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import httpx
import typer

DEFAULT_BACKEND_URL = "http://127.0.0.1:8080"
_SUMMARY_WIDTH = 100


def _summarize(payload: Any) -> str:
    if not payload:
        return ""
    if isinstance(payload, dict):
        summary = ", ".join(f"{key}={value}" for key, value in payload.items())
    else:
        summary = str(payload)
    return summary[:_SUMMARY_WIDTH]


async def _trace_async(backend_url: str, case_id: str) -> int:
    async with httpx.AsyncClient(base_url=backend_url) as client:
        try:
            response = await client.get(f"/cases/{case_id}/events")
        except httpx.HTTPError as exc:
            typer.echo(f"error: backend unreachable at {backend_url}: {exc}", err=True)
            return 2
    if response.status_code == 404:
        typer.echo(f"error: case {case_id!r} not found", err=True)
        return 1
    response.raise_for_status()
    body = response.json()
    events = body.get("events", []) if isinstance(body, dict) else list(body)

    typer.echo(f"trace for case {case_id} ({len(events)} events)")
    for event in events:
        ts = event.get("ts", "")
        actor = event.get("actor", "")
        event_type = event.get("type", "")
        summary = _summarize(event.get("payload"))
        line = f"{ts}  {actor:<24}{event_type:<28}{summary}"
        typer.echo(line.rstrip())
    if not events:
        typer.echo("(no events)")
    return 0


def trace_command(
    case: Annotated[str, typer.Option("--case", help="Case id to trace.")],
    backend_url: Annotated[
        str, typer.Option("--backend-url", help="Backend base URL.")
    ] = DEFAULT_BACKEND_URL,
) -> None:
    """Print the chronological event timeline for a case."""
    exit_code = asyncio.run(_trace_async(backend_url, case))
    if exit_code:
        raise typer.Exit(exit_code)
