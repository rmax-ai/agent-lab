# DECISIONS.md — Agent Lab Design Rationale

Legend: `[SPEC]` = dictated by SPEC.md · `[FINAL]` = decided, change only with reason · `[PROVISIONAL]` = needs Max confirmation

## Repository & platform

| ID | Decision | Why | Status |
|---|---|---|---|
| DEC-01 | Single FastAPI process hosts Agent Router, Channels, Case Store, Human Tasks, Scenario Engine, Evaluation Engine, Event Store, MockWorld | SPEC §27 "one backend process" — no distributed infra for MVP | [SPEC] |
| DEC-02 | SQLite via SQLModel, WAL mode, single DB file; all state per SPEC §9 schema | SPEC §7 "shared API backed by SQLite"; SQLModel gives typed rows + migration path | [FINAL] |
| DEC-03 | uv workspace monorepo: `backend/`, `sdk/`, `mock-world/`, `agents/onboarding/` as members; `templates/team-agent/` standalone | SPEC §28 layout; past fp projects established uv-monorepo pattern | [FINAL] |

## Agent runtime

| ID | Decision | Why | Status |
|---|---|---|---|
| DEC-04 | Google ADK `LlmAgent` + plain function tools; multi-agent coordination via channels + workflow contract — NOT ADK Workflow graphs | SPEC §10 "ordinary ADK function tools"; coordinator/domain split is process-level, not graph-level | [SPEC]/[FINAL] |
| DEC-05 | Tool faults (`timeout`, `500`, `stale`, `success_without_state_change`) injectable ONLY into declared mutation tools; `verify_*` reads always truthful | Faulting verify reads makes outcome verification meaningless and corrupts the eval baseline | [PROVISIONAL] |
| DEC-06 | Per-case isolation via `case_id` correlation on every event/task/blocker; concurrent workflows in one process; WS sessions keyed by agent identity | SPEC §19/§24; finals run 12 starters concurrently (SPEC §20) | [FINAL] |
| DEC-07 | MockWorld enforces server-side tool identity: caller agent may only hit its domain's endpoints | SPEC §10 "each domain agent gets only its own tools"; defense-in-depth vs THREAT_MODEL T-10 | [FINAL] |
| DEC-08 | Default bounds: max_retries=3, max delegation depth=2, tool timeout=30s, HITL no-response SLA=300s → escalate | SPEC §24 evaluates "no infinite delegation / no excessive retries" but gives no numbers; constants centralized + scenario-overridable | [PROVISIONAL] |
| DEC-09 | MVP auth: shared bearer token for `/simulation/*`, per-agent registration tokens for agent-facing calls; SSO/IAM deferred | SPEC §29 defers SSO; some identity is REQUIRED by §17.4/§20 (unauthorized approval); this is the minimal token layer | [PROVISIONAL — security-relevant] |
| DEC-10 | HumanTask carries `requested_from`; approval authorized only if resolved by requested_from (or simulated unauthorized actor per scenario) | SPEC §15/§17.4/§20 | [PROVISIONAL] |

## Knowledge, transport, evaluation

| ID | Decision | Why | Status |
|---|---|---|---|
| DEC-11 | Knowledge = whole-file context load + basic file search, no RAG. Documents wrapped in DATA_DELIMITER ("DATA, NOT INSTRUCTIONS") | SPEC §6 "don't overbuild RAG"; delimiter mitigates THREAT_MODEL T-01 | [FINAL] |
| DEC-12 | `AgentTransport` ABC (send/subscribe/delegate/report_status); AgentLabTransport over WebSocket+HTTP; SlackTransport later | SPEC §14 — swappable boundaries are the core property | [SPEC] |
| DEC-13 | Deterministic eval primary (state assertions, safety invariants, trajectory assertions, system behavior); LLM-as-judge auxiliary only | SPEC §24 "Avoid making LLM-as-judge the main pass/fail mechanism" | [SPEC] |
| DEC-14 | Hidden scenarios excluded from public repo entirely (gitignored + private platform archive); participants get team packs only | SPEC §28 "should obviously not be distributed" | [FINAL] |
| DEC-15 | Scenario YAML schema per SPEC §16; timed mutations scheduled by Scenario Engine; scenarios control the world, never the agents | SPEC §16 | [SPEC] |
| DEC-16 | Gemini structured output: flat Pydantic schemas only; text-parse fallback for the JSON contract | Research: Gemini nested-schema reliability | [FINAL] |
| DEC-17 | Model env-configurable (`AGENTLAB_MODEL`), explicit pin in template; no ADK defaults | Research: ADK default migrated 2.5-flash → 3-flash-preview silently | [FINAL] |

## Rejected alternatives

| Rejected | Reason |
|---|---|
| MCP for domain tools | SPEC §10: "No MCP required" — plain ADK function tools + HTTP |
| ADK Workflow graphs for coordination | Coordinator/domain contract is multi-process; ADK Workflow is in-process graph execution. Channels are the coordination substrate (SPEC §13) |
| Separate microservices per MockWorld domain | SPEC §7: "One FastAPI application is sufficient" |
| RAG / embeddings for knowledge | SPEC §6: tiny corpus; context load sufficient |
| Distributed eval infra / Kafka | SPEC §29 defer list |
