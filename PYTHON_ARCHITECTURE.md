# PYTHON_ARCHITECTURE.md — Workspace Layout & Dependency Direction

## Workspace (uv monorepo, DEC-03)

```
agent-lab/
├── pyproject.toml            # root workspace
├── backend/                  # FastAPI app (Agent Router, Channels, Cases, HumanTasks,
│                             #  ScenarioEngine, EvaluationEngine, EventStore) + hosted MockWorld router
├── sdk/                      # agentlab.sdk: protocols, transport, knowledge, client, TeamAgent
├── mock-world/               # agentlab.world: SQLite schema, seeds, /world/* + /simulation/* routers
├── agents/onboarding/        # Onboarding Agent (in-process, hosted by backend)
├── templates/team-agent/     # starter repo scaffold (own pyproject; copied out, NOT a workspace member)
├── knowledge/                # Markdown corpora (data, not packages)
├── scenarios/                # YAML (data)
└── frontend/                 # React/Vite (pnpm; separate toolchain)
```

## Dependency direction (hard rule)

```
sdk        → nothing (pure protocols + abstract providers)
mock-world → nothing internal (standalone FastAPI app)
backend    → sdk, mock-world
agents/*   → sdk ONLY (never backend, never mock-world internals)
templates  → sdk (published/installed)
```

- No agent module imports anything from `backend/`. Team agents run on laptops and must work with only the SDK installed.
- MockWorld's SQLite is opened by `agentlab.world` alone. Backend talks to it via HTTP like everyone else — this keeps the "agents vs reality" boundary real even in-process (SPEC §7, §9).

## Process topology (DEC-01)

One backend process on the shared machine:

```
uvicorn agentlab.backend.main:app
  ├── /world/*        (MockWorld agent-facing router)
  ├── /simulation/*   (privileged)
  ├── /cases /channels /tasks /scenarios /evals
  ├── WS /ws/agents   (agent transport hub)
  ├── hosted Onboarding Agent (InMemoryRunner, in-process)
  └── SQLite (WAL)
```

Team agents on laptops: their own `uv run agent-lab dev` starts a WS client (AgentLabTransport) + local ADK agent + MarkdownKnowledgeProvider.

## Startup contract (SPEC §26)

`agent-lab dev` in a team template must print, on success:

```
✓ connected to Agent Lab
✓ MockWorld available
✓ knowledge loaded: N documents
✓ tools registered: N
✓ <agent-id> ONLINE
```

## Boundaries checklist (SPEC §4 — the property everything else serves)

- [ ] `KnowledgeProvider` ABC: Markdown impl only; Confluence later, same `search()/get_document()`
- [ ] `AgentTransport` ABC: AgentLabTransport only; Slack later, same send/subscribe/delegate/report_status
- [ ] Domain tools: MockWorld HTTP impl only; direct API / MCP later, same tool signatures
- [ ] Scenario Engine: lab-only; production = pre-production assurance harness (SPEC §30)
