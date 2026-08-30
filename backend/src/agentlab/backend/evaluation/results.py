"""SPEC §25 scenario-result rendering.

Plain-text only, fully deterministic: no colours, no rich, no ANSI escapes. The
summary table matches the SPEC §25 layout; the failed-scenario diff view lists
each required event with its observed value (or ``none``) and the final state.
"""

from __future__ import annotations


def render_summary_table(
    agent_name: str,
    rows: list[tuple[str, bool]],
) -> str:
    """Render the per-agent summary table and the ``n / m`` line (SPEC §25)."""
    width = max([len("Scenario")] + [len(name) for name, _ in rows]) + 2
    lines = [agent_name.upper()]
    lines.append(f"{'Scenario':<{width}}Result")
    for name, passed in rows:
        lines.append(f"{name:<{width}}{'PASS' if passed else 'FAIL'}")
    passed_count = sum(1 for _, passed in rows if passed)
    lines.append(f"{passed_count} / {len(rows)}")
    return "\n".join(lines)


def render_failed_diff(
    scenario_id: str,
    required_events: list[str],
    observed_events: list[str],
    final_state: str | None,
) -> str:
    """Render the §25 diff view for one failed scenario."""
    del scenario_id  # the id is not part of the SPEC §25 diff body
    observed_set = set(observed_events)
    lines = []
    for event in required_events:
        observed = event if event in observed_set else "none"
        lines.append(f"Expected: {event}  Observed: {observed}")
    lines.append(f"Final state: {(final_state or 'NONE').upper()}")
    lines.append("Trace: [open]")
    return "\n".join(lines)
