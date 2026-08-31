# Participant runbook

The team development loop for Agent Lab (SPEC §26). You build a domain agent
from the team template, run it against the in-process lab, and certify it
against your domain's scenario pack. Everything below matches the shipped
CLI (`cli/src/agentlab/cli/`) and template (`templates/team-agent/`).

## The loop

```
edit → run → scenario → inspect trace → improve
```

### 1. Scaffold

```bash
agent-lab init my-agent
cd my-agent
uv sync
```

`agent-lab init <name>` copies `templates/team-agent/` into `<name>/`,
including its canonical `pyproject.toml`. The template's uv git-subdirectory
sources point at the public agent-lab repo (`agentlab-cli`, `agentlab-sdk`,
`agentlab-backend`, `agentlab-world`), so the fresh copy stands alone:
`uv sync && agent-lab dev` works anywhere. (Run `init` from inside the
agent-lab repo so it can find the template directory.)

What's in the scaffold:

- `agent.py` — `build_team_agent()`: id, goal, `instructions.md`, a
  `MarkdownKnowledgeProvider("./knowledge")`, a `tools` list, and an explicit
  model from `AGENTLAB_MODEL` (DEC-17: no silent ADK default).
- `instructions.md` — the agent's system instructions (goal, workflow,
  policy, HITL, verification).
- `knowledge/` — your Markdown corpus (frontmatter + body; see
  `knowledge/devices/` at the repo root for working examples).
- `tools/example.py` — the MockWorld HTTP tool pattern: plain async function,
  docstring as the tool description, flat dict returns, no raw HTTP errors
  leaking to the model.

### 2. Boot the lab

```bash
agent-lab dev
```

`dev` boots the real backend (port 8080) and the real MockWorld (port 8000)
in-process with uvicorn on 127.0.0.1, sets `MOCKWORLD_URL` for your tools,
imports your `agent.py`, registers the agent (`POST /agents/register`),
completes the WebSocket hello → welcome handshake, and probes MockWorld.
Startup output (printed only after every check really passed):

```
✓ connected to Agent Lab
✓ MockWorld available
✓ knowledge loaded: 6 documents
✓ tools registered: 5
✓ device-agent ONLINE
```

(your counts and agent id will differ).

### 3. Edit, restart, test

Edit `instructions.md`, `knowledge/*.md`, `agent.py` (and your `tools/`).
Ctrl-C stops `dev`; re-run it to pick up changes. Then run a scenario and
inspect the trace:

```bash
agent-lab scenario run --scenario scenarios/devices/01_happy_path.yaml
agent-lab trace --case ONB-E42
agent-lab status
```

## CLI reference

### `agent-lab dev`

Boot the lab in-process and bring the current directory's agent online.

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `8080` | backend port |
| `--world-port` | `8000` | MockWorld port |
| `--once` | off | print the startup checks and exit (smoke check) |

Errors: no `agent.py` in the cwd, an `agent.py` that fails to import, or one
without `build_team_agent()` all exit 2 with a message. If the hub never
sends a `welcome` frame, `dev` fails rather than pretending to be online.

### `agent-lab init <name>`

Copy the team-agent template into `<name>/` in the current directory. Fails
if the target exists and is non-empty, or if `templates/team-agent/` can't be
found (run from the agent-lab repo). Prints the next step:
`cd <name> && uv sync && agent-lab dev`.

### `agent-lab scenario run`

Run one scenario against the in-process lab and print the score.

| Flag | Default | Meaning |
|---|---|---|
| `--scenario PATH` | **required** | path to the scenario YAML |
| `--agent NAME` | `device` | agent under test; only `device` ships today (anything else exits 2) |
| `--scripted` / `--no-scripted` | `--scripted` | deterministic no-LLM trajectory. `--no-scripted` exits 2: real-agent mode is not implemented and no LLM is configured |
| `--port` | `8080` | backend port |
| `--world-port` | `8000` | MockWorld port |

Scripted mode runs a canned trajectory through the real in-process MockWorld
and backend, driven by the ScenarioEngine and scored by the EvaluationEngine
(SPEC §16/§24/§25) — no live LLM calls, ever. Output is the SPEC §25-style
summary: scenario verdict, per-category breakdown (weight and violations),
`score: X / 100.0 (threshold 70.0)`, `passed`, and the final state. Exit
code: 0 on PASS, 1 on FAIL, 2 on errors (bad YAML, unknown scenario id for
the scripted driver, …).

The CLI's scripted driver currently covers `device-01-happy-path`; the full
packs run under pytest (see Certification). Timed mutations are compressed
with `time_scale` 0.02, so an `at: 30` mutation lands ~0.6s into the run.

### `agent-lab trace`

Print a case's chronological event timeline from the backend Event Store
(SPEC §23).

| Flag | Default | Meaning |
|---|---|---|
| `--case ID` | **required** | case id to trace (e.g. `ONB-E42`) |
| `--backend-url` | `http://127.0.0.1:8080` | backend base URL |

Reads `GET /cases/{case_id}/events` and prints `ts`, `actor`, `type`, and a
payload summary per event. Exit 1 if the case is not found, 2 if the backend
is unreachable. Note: `trace` reads a **running** backend — keep
`agent-lab dev` up in another terminal.

### `agent-lab status`

Probe the lab and list the agent registry.

| Flag | Default | Meaning |
|---|---|---|
| `--backend-url` | `http://127.0.0.1:8080` | backend base URL |
| `--world-url` | `http://127.0.0.1:8000` | MockWorld base URL |

Probes `GET /agents` on the backend and `/openapi.json` on MockWorld, then
prints each registered agent's id, status, tool count, and knowledge-doc
count. Exit 0 when both are reachable, 1 otherwise.

## Certification

Each domain has a **certification pack** (SPEC §18): five scenarios under
`scenarios/<domain>/`, run by `agents/<domain>/tests/test_certification_pack.py`:

```bash
uv run pytest agents/device/tests/test_certification_pack.py
uv run pytest agents/access/tests/test_certification_pack.py
uv run pytest agents/systems/tests/test_certification_pack.py
uv run pytest agents/applications/tests/test_certification_pack.py
```

Each pack test parametrizes over the pack's YAML files, runs a scripted
deterministic agent trajectory through the real MockWorld tools and real
backend routes, and scores the run with the EvaluationEngine. **A PASS
means**: every `required_events` trajectory event was observed, the final
workflow state is in `allowed_final_states`, no `forbidden_events` safety
invariant fired, and the weighted SPEC §24 score meets the threshold (70/100
by default). Packs are the gate for joining the final simulation, together
with the SPEC §19 contract certification suite at
`agents/<domain>/tests/test_contract_certification.py`.

The four domains exercise deliberately different world contracts:

- **Device** — full mutator: reserve/replace routes with inventory
  consequences; delivery failures surface through truthful reads.
- **Access** — request flow: the agent creates access requests, and the
  world's IAM backend grants them via timed mutations; privileged groups are
  gated on a manager APPROVAL human task created *before* the world request.
- **Systems** — read-only world (`GET /world/systems/{id}` only). There is
  no provisioning route: `provision_account` opens an IT provisioning
  **HumanTask** through the backend task flow (`requested_from: it-support`),
  and the account materializes later as a world-state change performed by
  IT/the harness, discovered through truthful reads. Agents never create or
  activate SystemAccount rows.
- **Applications** — full mutator with an idempotent grant route and no
  revoke route: the agent provisions only what the role→application mapping
  requires (GitHub is engineering-only), and verifies before completing.

Multi-agent integration scenarios run under
`agents/onboarding/tests/test_integration_scenario.py` (the committed
four-domain scenario onboards five employees through the real coordinator
and all four real domain agents); the DEC-14 hidden runner (skips unless the
platform archive is present) is
`agents/onboarding/tests/test_hidden_scenarios.py`.

## Troubleshooting

| Symptom | Check |
|---|---|
| `✗ MockWorld unavailable` in `dev` output | nothing else squatting on `--world-port` (default 8000); the probe is `GET /openapi.json` |
| Tool calls hit the wrong world | tools read `MOCKWORLD_URL` **at import time** (default `http://localhost:8000`); `agent-lab dev` sets it for you — set it yourself when running tools outside `dev` |
| Model errors / surprising model | `AGENTLAB_MODEL` is read explicitly by the template and passed to the agent (DEC-17); unset means the SDK's configured behavior — pin it |
| Wrong agent identity | `AGENTLAB_AGENT_ID` / `AGENTLAB_GOAL` env vars override the template defaults; the id must match the world's `ALLOWED_DOMAINS` entries for domain routes to allow it — the lab's canonical four-domain value is `device-agent:devices,access-agent:access,systems-agent:systems,applications-agent:applications` (code default: `device-agent:devices`) |
| 401 from `/simulation/*` | the world requires `Authorization: Bearer $SIMULATION_TOKEN` (default `dev-token`); the CLI's scenario runner sends `SIMULATION_TOKEN` from the environment, defaulting to `dev-token` — keep both sides in sync |
| 403 `UNAUTHORIZED_APPROVER` on a task decision | DEC-10: only the task's `requested_from` principal may resolve it (unless `ALLOW_ANY_RESOLVER=1` is set on the backend) |
| Console shows no data | the frontend talks to `VITE_API_BASE` (default `http://localhost:8080`) and `VITE_WORLD_BASE` (default `http://localhost:8000`); CORS is open to the vite dev origin (`localhost:5173`). For a backend-free UI demo, `VITE_MOCK=1` puts the console in mock mode (canned frontend data, clearly not the lab) |
| Database location | one shared SQLite file, path from `AGENTLAB_DB` (default `./agent-lab.db`), WAL mode; delete it to start completely clean |

Environment variable summary:

| Variable | Consumed by | Default |
|---|---|---|
| `MOCKWORLD_URL` | team tools, ScenarioEngine | `http://localhost:8000` |
| `AGENTLAB_MODEL` | template `agent.py` | unset (DEC-17: pin it) |
| `AGENTLAB_AGENT_ID` / `AGENTLAB_GOAL` | template `agent.py` | `team-agent` / `team_goal` |
| `SIMULATION_TOKEN` | MockWorld `/simulation/*`, CLI runner | `dev-token` |
| `AGENTLAB_SIMULATION_TOKEN` | ScenarioEngine bearer token when none is passed explicitly | unset |
| `AGENTLAB_DB` | backend + MockWorld SQLite path | `./agent-lab.db` |
| `ALLOWED_DOMAINS` | MockWorld domain enforcement | `device-agent:devices` (canonical lab value: `device-agent:devices,access-agent:access,systems-agent:systems,applications-agent:applications`) |
| `ALLOW_ANY_RESOLVER` | backend task decisions | unset (DEC-10 enforced) |
| `AGENTLAB_HIDDEN_DIR` | hidden-scenario runner | `scenarios/hidden/` |
| `VITE_API_BASE` / `VITE_WORLD_BASE` / `VITE_MOCK` | frontend console | `localhost:8080` / `localhost:8000` / off |
