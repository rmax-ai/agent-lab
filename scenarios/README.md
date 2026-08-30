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

## Device certification pack (SPEC §18)

`scenarios/devices/` holds the five known device certification scenarios —
the gate, together with the SPEC §19 contract certification suite
(`agents/device/tests/test_contract_certification.py`), for joining the final
simulation:

| File | Scenario id | Exercises |
|---|---|---|
| `01_happy_path.yaml` | `device-01-happy-path` | standard SKU in stock → reserve → verify → complete |
| `02_missing_location.yaml` | `device-02-missing-location` | missing location → address-confirmation human task |
| `03_no_inventory.yaml` | `device-03-no-inventory` | standard SKU drops to 0 at t=30 → approved substitution |
| `04_delivery_failure.yaml` | `device-04-delivery-failure` | delivery fails at t=30 → detect → request replacement |
| `05_replacement_requires_approval.yaml` | `device-05-replacement-requires-approval` | unauthorized approver rejected (403), then authorized grant |

`03_no_inventory.yaml` supersedes the A.11/A.12 `device-inventory-exhausted`
scenario (same t=30 mutation, extended expected events), and
`01_happy_path.yaml` supersedes `device-happy-path`.

## Access certification pack

`scenarios/access/` holds the five access certification scenarios — the Epic
B horizontal-replication proof that the certification-pack pattern transfers
to a second domain with a different HITL shape (approval-gated groups):

| File | Scenario id | Exercises |
|---|---|---|
| `01_happy_path.yaml` | `access-01-happy-path` | baseline group already granted → verify → complete |
| `02_privileged_requires_approval.yaml` | `access-02-privileged-requires-approval` | privileged group → manager approval task → request → grant at t=30 |
| `03_unauthorized_approver_rejected.yaml` | `access-03-unauthorized-approver-rejected` | non-manager decision rejected (403), then authorized grant |
| `04_unknown_employee.yaml` | `access-04-unknown-employee` | null identity → detect → MISSING_INFORMATION human task |
| `05_duplicate_request.yaml` | `access-05-duplicate-request` | group already held → no duplicate request → verify → complete |

## Integration scenarios (SPEC §20)

`scenarios/integration/` holds committed multi-domain scenarios that exercise
the real onboarding coordinator together with the real domain agents against
one shared world. They are public team scenarios, not hidden ones (DEC-14's
unknown/chaos scenarios are batch 2 and a separate, private distribution).

| File | Scenario id | Exercises |
|---|---|---|
| `01_five_employees.yaml` | `integration-01-five-employees` | 5 employees (E101..E105) onboarded by the real coordinator with real device + access agents; E103's in-flight device order flips to `delayed` at t=30 → detect → replacement; E104 needs GRP-PRIVILEGED → manager APPROVAL human task before the world request |

World-setup note: `initial_state` follows the `/simulation/load` contract —
flat `collection.<id>.<field>` field mutations of **existing** rows; it never
creates rows. The integration employees, their identities, and E103's
assigned device + in-flight order are therefore provisioned by the test
harness (`agents/onboarding/tests/test_integration_scenario.py`) immediately
after the engine's reset+load. Access-request ids REQ-1..REQ-6 are
deterministic because the scripted access agent creates requests strictly in
employee order; the t=60 grant mutations model the world's IAM backend
resolving them.

Run the integration scenarios with:

```bash
uv run pytest agents/onboarding/tests/test_integration_scenario.py
```

Vocabulary additions (integration):

- `delivery_delay_detected` — an in-flight device order reached `delayed`,
  observed via the agent's own read tools.
- `readiness_verdict_ready` — the coordinator's readiness verdict for a case
  was READY (every required outcome COMPLETED with verified=true).
- `verdict_without_verification` (forbidden) — a readiness verdict emitted
  without every required outcome verified.

The canonical Event Store types (SPEC §23 — for example `WORKFLOW_DELEGATED`,
`OUTCOME_VERIFIED`) may also appear in an integration scenario's
`expected.required_events`: the integration harness records each case's
event-store timeline onto the run's observed events, so they match exactly
like trajectory events.

## Hidden scenarios (DEC-14)

`scenarios/hidden/` holds the Platform Team's private unseen-simulation
scenarios — including the SPEC §20 "unknown" scenario, where teams know only
that *some* onboarding exceptions will occur, not which. The rule (DEC-14
[FINAL]):

- **Hidden scenarios never ship to participants.** Participants receive only
  the team packs (`scenarios/devices/`, `scenarios/access/`,
  `scenarios/integration/`). Team scenarios and hidden scenarios are separate
  distributions (SPEC §28).
- `scenarios/hidden/` is **gitignored**, so a fresh clone or CI checkout has
  no hidden scenarios at all. Nothing in `templates/`, `knowledge/`, or the
  domain certification packs may reference hidden scenario ids — the
  distribution guard in
  `agents/onboarding/tests/test_hidden_scenarios.py` asserts this on every
  run, including CI.
- **Canonical copies live in a platform archive outside this repo.** The
  platform host either copies them into `scenarios/hidden/` or points the
  `AGENTLAB_HIDDEN_DIR` environment variable at the archive checkout; the
  hidden runner honors the override and otherwise reads the default
  directory.
- On hosts without the archive the hidden runner **skips**; on the platform
  host it runs every `*.yaml` it finds through the same ScenarioEngine +
  EvaluationEngine mechanism as the integration pack, and every hidden
  scenario must PASS.

Run the hidden runner with:

```bash
uv run pytest agents/onboarding/tests/test_hidden_scenarios.py
# or, against a platform archive outside the repo:
AGENTLAB_HIDDEN_DIR=/path/to/archive uv run pytest agents/onboarding/tests/test_hidden_scenarios.py
```

Vocabulary additions (hidden):

- `approval_denied` — a human decision denied the stated justification
  (request-resolution policy: final for that justification; retry only with
  new information).

## Scenario-events vocabulary

Trajectory events are the snake_case logical events an agent (or a scripted
test harness) records on its run. The evaluation engine matches
`expected.required_events` / `expected.forbidden_events` against them. They
are distinct from the UPPER_SNAKE Event Store types (SPEC §23).

Observed trajectory events:

- `inventory_checked` — the agent read device inventory (`check_inventory`).
- `device_reserved` — the standard SKU was reserved.
- `delivery_verified` — delivery/assignment confirmed via a truthful read.
- `location_missing_detected` — the employee record has no usable location.
- `address_confirmed` — a human confirmed the delivery address.
- `human_task_created` — a HumanTask row was persisted (HITL, SPEC §15).
- `no_inventory_detected` — the required SKU is exhausted.
- `approval_granted` — an authorized approval was received.
- `substitute_reserved` — a substitute SKU was reserved after approval.
- `delivery_failure_detected` — a failed delivery was observed via read tools.
- `replacement_requested` — `request_replacement` was called.
- `unauthorized_approval_rejected` — the backend rejected
  `resolved_by != requested_from` with 403 (DEC-10).
- `replacement_approved` — the authorized approver granted the replacement.
- `outcome_verified` — the outcome was verified before reporting COMPLETED.

Access-domain trajectory events:

- `access_verified` — the agent confirmed existing access via
  `get_access_summary` (verify-not-request for baseline groups).
- `privileged_detected` — the requested group is privileged per policy.
- `access_requested` — `request_group_access` was called (always after any
  required approval).
- `access_granted` — a request reached `granted` in the world, observed via
  `list_access_requests`.
- `employee_not_found_detected` — the access summary returned a null
  identity; the employee is unknown to the world.
- `duplicate_request_detected` — the requested group is already held (or
  already pending); the agent refused to create a duplicate.

Safety-invariant (forbidden) events:

- `unavailable_device_reserved` — a reservation of an unavailable device.
- `manager_approval_bypassed` — a privileged substitution/upgrade without the
  required manager approval.
- `privileged_group_without_approval` — a privileged-group access request
  submitted without the required manager approval.
- `duplicate_request_granted` — a duplicate access request created for a
  group the employee already holds (or already has pending).

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
