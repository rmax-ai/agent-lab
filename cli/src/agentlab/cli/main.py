"""``agent-lab`` CLI entry point (SPEC §26).

The local development loop: ``dev`` boots the lab in-process and brings the
current directory's team agent online; ``scenario run`` executes a scenario
deterministically; ``trace`` inspects a case's event timeline; ``status``
probes the lab; ``init`` scaffolds a new team-agent project.
"""

from __future__ import annotations

import typer

from agentlab.cli.dev import dev_command
from agentlab.cli.init import init_command
from agentlab.cli.scenario import scenario_run_command
from agentlab.cli.status import status_command
from agentlab.cli.trace import trace_command

app = typer.Typer(
    name="agent-lab",
    help="Agent Lab local development experience (SPEC §26).",
    no_args_is_help=True,
)

scenario_app = typer.Typer(help="Run scenarios against the lab.", no_args_is_help=True)
scenario_app.command("run")(scenario_run_command)

app.command("dev")(dev_command)
app.command("init")(init_command)
app.command("trace")(trace_command)
app.command("status")(status_command)
app.add_typer(scenario_app, name="scenario")


if __name__ == "__main__":
    app()
