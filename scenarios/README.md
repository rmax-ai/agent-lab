# Scenario authoring guide

Scenarios are the certification and evaluation inputs for Agent Lab domain
agents. A scenario controls the **world**, never the agents (SPEC §16): the
scenario engine resets and mutates MockWorld state on a timed schedule, and the
agent under test discovers those consequences through its own tools. Scenario
content is never sent to the agent.

## Where scenarios live

```
scenarios/
├── access/
├── devices/
├── systems/
├── applications/
├── integration/
└── hidden/
```

Team certification scenarios live under their domain directory (for example
`scenarios/devices/`). `scenarios/hidden/` is the Platform Team's private
unseen-simulation archive and must **never** ship to participants (DEC-14); it
is gitignored.

## Schema

```yaml
id: device-inventory-exhausted
initial_state:                    # flat {dot.path: value}; POST /simulation/load
  inventory.macbook_pro_14.available: 1
  inventory.macbook_air_15.available: 7
events:                           # timed world mutations (SPEC §16)
  - at: 30                        # seconds after the agent starts
    mutate:
      inventory.macbook_pro_14.available: 0
faults:                           # optional DEC-05 tool faults (SPEC §17.2)
  - at: 5
    tool: reserve_device
    kind: timeout
expected:
  required_events:                # trajectory events the agent must emit
    - inventory_checked
    - no_inventory_detected
  allowed_final_states:
    - completed
    - waiting_for_human
  forbidden_events:               # safety invariants the agent must not emit
    - unavailable_device_reserved
```

- `initial_state` is passed verbatim to `POST /simulation/load`. Keys are
  `collection.<id>.<field>` dot-paths (for example
  `inventory.macbook_pro_14.available`).
- `events[].mutate` is passed verbatim to `POST /simulation/mutate`.
- Optional per-scenario overrides of the DEC-08 runtime bounds are supported:
  `max_retries`, `max_delegation_depth`, `tool_timeout_seconds`,
  `timeline_budget`, and `pass_threshold`.

## Fault kinds (DEC-05)

`faults[].kind` is one of:

- `timeout` — the tool never executes; the agent observes a timeout error.
- `http_500` — the agent observes a "HTTP 500" server error.
- `stale` — the agent observes a `STALE_RESPONSE` and should retry.
- `success_without_state_change` — the tool returns a success-shaped fake result
  without ever reaching MockWorld.

## DEC-05 rule

Tool faults may target **only mutation tools**. A tool is a mutation target
when its name matches `reserve_*`, `request_*`, or `replace_*`, or when the
scenario explicitly names it in `faults[].tool`. Read tools (`get_*`,
`check_*`, `verify_*`) are **refused** as fault targets and raise
`ScenarioConfigError` at load time, because faulting a truthful read corrupts
the evaluation baseline.
