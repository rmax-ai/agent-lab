# AGENTS.md — Agent Lab

Conventions for autonomous coding agents (Codex / Droid) working in this repo. Read before touching anything.

## What this repo is

Agent Lab: a development, simulation, and evaluation environment for team-owned operational AI agents. First business process: **monthly employee onboarding**. The coordinator (Onboarding Agent) owns the process; domain agents (Access, Device, Systems, Applications) own their workflows; all external reality flows through MockWorld (SQLite-backed FastAPI). Ground truth: `SPEC.md`. Architecture: `docs/ARCHITECTURE.md`. Threats: `docs/THREAT_MODEL.md`. Plan: `docs/ROADMAP.md`.

## Architecture non-negotiables

Violating any of these fails review, regardless of tests:

1. **Boundary swappability.** Agents depend only on abstract interfaces — `KnowledgeProvider`, `DomainTool`, `AgentTransport`. Concrete adapters (Markdown, MockWorld HTTP, AgentLabTransport) are injected. No agent imports a concrete adapter directly. This is "probably the most important engineering property of the lab" (SPEC §4).
2. **MockWorld is the only external reality.** Agents reach it ONLY through their ADK function tools. SQLite is never exposed directly to agents (SPEC §9).
3. **Simulation APIs are privileged.** `/simulation/*` endpoints must be unreachable by agent tools — enforced by design (separate route groups + no tool wrappers), and asserted in tests.
4. **Deterministic evaluation is primary.** State assertions, safety invariants, trajectory assertions (SPEC §24). LLM-as-judge may be auxiliary evidence, never the pass/fail mechanism.
5. **HITL is persisted state.** HumanTask rows + workflow status `WAITING_FOR_HUMAN`, resolved via decision → event → resume. Never chat improvisation (SPEC §15).
6. **Everything correlates with `case_id` and `workflow_id`.** Events, tasks, blockers, messages (SPEC §19).
7. **Vertical-first.** Complete the Onboarding → Device → Markdown → tool → MockWorld → HITL → verification → eval slice before adding Access/Systems/Applications breadth (SPEC §30).
8. **Hidden scenarios stay hidden.** `scenarios/hidden/` must never ship to participants (gitignored or private archive). Team scenarios and hidden scenarios are separate distributions (SPEC §28).

## Stack (SPEC §27)

- Python 3.12+, Google ADK for agents
- FastAPI backend (single process: Agent Router, Channels, Case Store, Human Tasks, Scenario Engine, Evaluation Engine, Event Store, MockWorld)
- Pydantic v2 schemas · SQLite persistence · YAML scenario definitions
- WebSocket + HTTP for agent communication
- React/Vite frontend · pytest for evaluation
- No distributed infrastructure in MVP (no MCP, Kafka, K8s, Slack, Confluence)

## Repo layout (SPEC §28)

```
backend/     {agents,channels,cases,human_tasks,scenarios,evaluation,events,world}
frontend/    React/Vite UI
sdk/         {agent.py,client.py,transport.py,knowledge.py,protocols.py}
templates/   team-agent starter repo
knowledge/   {onboarding,access,devices,systems,applications} Markdown corpora
scenarios/   {access,devices,systems,applications,integration,hidden}
mock-world/  {schema.sql,seeds}
agents/      onboarding coordinator
```

## Tooling conventions

- `uv` for environment management (`uv sync`); package manager lockfiles gitignored where appropriate
- Lint: `ruff` · Types: `ty` · Tests: `pytest`
- Scenario definitions are YAML with `initial_state`, `events`, `expected` (SPEC §16)
- Events written to the Event Store via a single helper — trace timeline is the debugging foundation (SPEC §23)

## Testing requirements

- Every scenario ships with executable assertions for `required_events`, `allowed_final_states`, `forbidden_events` (SPEC §16)
- Contract certification suite (SPEC §19 checklist) exists as pytest
- Safety invariants asserted in eval: no privileged access without approval, no unavailable device reserved, no duplicate provisioning
- Workflow contract state machine tested for all transitions (SPEC §12)

## PR gates

```bash
uv sync --all-packages && uv run ruff check sdk/src backend/src mock-world/src agents/onboarding/src && uv run ty check sdk/src backend/src mock-world/src agents/onboarding/src && uv run pytest
```

All green before merge. Frontend: typecheck + tests as applicable. `uv sync --all-packages` is required — the root project is virtual and does not auto-install workspace members.

## Secrets

- `.envrc` + `direnv` for local secrets; never commit credentials or embed `pass`/`gpg` calls in application code (see `direnv-project-secrets` skill).

## Delegation conventions

- Python work → Droid. TypeScript/React work → Codex. Hermes plans, reviews, verifies.
- Read the phase/story issue body before starting (it carries AC, dependencies, verification commands).
- Deterministic infrastructure fixes (build gates, scaffolding) are done directly by Hermes, not delegated.

## Companion docs

- [DECISIONS.md](DECISIONS.md) — design rationale; DEC-xx IDs referenced throughout. Respect [FINAL]; question [PROVISIONAL].
- [PYTHON_DEVELOPMENT.md](PYTHON_DEVELOPMENT.md) — env, gates, async, error handling, ADK usage, testing
- [PYTHON_API_DESIGN.md](PYTHON_API_DESIGN.md) — protocol models, events, routes, tool signatures
- [PYTHON_SYSTEM_DESIGN_PATTERNS.md](PYTHON_SYSTEM_DESIGN_PATTERNS.md) — state machine, event store, scenario engine, HITL loop
- [PYTHON_ARCHITECTURE.md](PYTHON_ARCHITECTURE.md) — workspace layout, dependency direction, process topology
- [docs/research/phase-1-findings.md](docs/research/phase-1-findings.md) — version pins + ADK API facts; RE-VERIFY the listed ADK 2.8.0 items before Phase A implementation
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — read before touching `mock-world/`, `backend/scenarios/`, `backend/human_tasks/`
