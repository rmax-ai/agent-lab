"""``agent-lab init <name>`` — scaffold a team-agent project (SPEC §26/§28).

Copies ``templates/team-agent/`` — including its canonical ``pyproject.toml``,
whose uv git-subdirectory sources point at the public agent-lab repo — into
``<name>/`` so a fresh copy supports ``uv sync && agent-lab dev`` anywhere.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer

_TEMPLATE_PARTS = ("templates", "team-agent")


def _find_template_dir() -> Path | None:
    """Locate ``templates/team-agent`` relative to this file or the cwd."""
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for base in start.parents:
            candidate = base.joinpath(*_TEMPLATE_PARTS)
            if candidate.is_dir():
                return candidate
    return None


def init_command(
    name: Annotated[str, typer.Argument(help="Directory name for the new project.")],
) -> None:
    """Copy the team-agent template into ``<name>/`` in the current directory."""
    target = Path.cwd() / name
    if target.exists() and any(target.iterdir()):
        typer.echo(f"error: {target} already exists and is not empty", err=True)
        raise typer.Exit(2)

    template_dir = _find_template_dir()
    if template_dir is None:
        typer.echo("error: templates/team-agent not found (run from the agent-lab repo)", err=True)
        raise typer.Exit(2)

    shutil.copytree(template_dir, target, dirs_exist_ok=True)
    if not (target / "pyproject.toml").is_file():
        typer.echo("error: template is missing its canonical pyproject.toml", err=True)
        raise typer.Exit(2)

    typer.echo(f"created {target}")
    typer.echo(f"next: cd {name} && uv sync && agent-lab dev")
