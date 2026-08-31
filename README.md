# Enterprise Agent Lab

Development, simulation, and evaluation environment for team-owned operational AI agents.

**Agent Lab** is a self-contained simulation harness around [Google ADK](https://google.github.io/adk-docs/). Teams receive an agent template, a Markdown knowledge base, typed tools backed by a shared MockWorld API, and certification scenarios. The first business process is **monthly employee onboarding**: an Onboarding Agent coordinates four domain agents (Device, Access, Systems, Applications — all built and certified) against a simulated reality — then the whole system must answer, deterministically: *"Are next Monday's new employees ready?"*

```
Onboarding Agent → delegates outcomes → Domain Agents → reason via knowledge + observed state → Domain Tools → MockWorld
```

The coordinator owns the process. Domain agents own their workflows.

## Quickstart

**Platform development (this repo):**

```bash
git clone https://github.com/rmax-ai/agent-lab.git && cd agent-lab
uv sync
uv run pytest                                  # 285 tests: unit + certification packs + finals
uv run agent-lab --help                        # dev / init / scenario run / trace / status
uv run agent-lab scenario run --scenario scenarios/devices/01_happy_path.yaml --scripted
```

**Team participants (build your own domain agent):**

```bash
cd agent-lab && uv run agent-lab init my-team-agent && cd my-team-agent
uv sync && uv run agent-lab dev
```

Expected startup output (SPEC §26):

```
✓ connected to Agent Lab
✓ MockWorld available
✓ knowledge loaded: 1 documents
✓ tools registered: 0
✓ team-agent ONLINE
```

Edit `instructions.md`, `knowledge/*.md`, `agent.py` — the loop is edit → run → scenario → inspect trace → improve. See the [participant runbook](docs/PARTICIPANT_RUNBOOK.md).

**Operator console:** `cd frontend && npm install && npm run dev` (backend + world must be running: `uv run agent-lab dev`). Live mode hits the backend on `:8080` and MockWorld on `:8000`; `VITE_MOCK=1` switches to mock replay.

## Key properties

| Property | Current | Later |
|---|---|---|
| Knowledge | MarkdownKnowledgeProvider | ConfluenceKnowledgeProvider |
| Domain tools | MockWorld HTTP | Direct APIs / MCP |
| Transport | AgentLabTransport | SlackTransport |
| State | SQLite | Durable workflow/event store |
| Scenario engine | Certification scenarios | Pre-production assurance harness |

Agents don't change when adapters swap — that boundary discipline is the core engineering property of the lab.

## Stack

Google ADK + Python · FastAPI backend · Pydantic · SQLite · YAML scenarios · WebSocket + HTTP · React/Vite · pytest + deterministic scenario evaluator. One backend process, no distributed infrastructure.

## Status

**Epic B complete: all four domain agents built and certified.** Vertical slice (onboarding → device → access → HITL → eval), horizontal replication proof (Access, Systems, Applications agents — including a read-only-world domain where provisioning flows through IT HumanTasks, and a full-mutator domain with an idempotent grant route), multi-agent finals (four-domain integration, unknown, chaos with readiness verdict + audit trail), CLI dev loop, operator console, hardening (event route, fault isolation, WS reconnect, CORS). The committed integration scenario onboards five employees through the real coordinator and all four real domain agents — green in CI. Board: [GitHub issues](https://github.com/rmax-ai/agent-lab/issues).

## Docs

- [SPEC.md](SPEC.md) — authoritative specification (30 sections)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system architecture
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — threat analysis
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased plan with acceptance criteria
- [AGENTS.md](AGENTS.md) — conventions for coding agents
- [docs/SCENARIO_AUTHORING.md](docs/SCENARIO_AUTHORING.md) — scenario YAML schema, faults, packs, hidden scenarios
- [docs/PARTICIPANT_RUNBOOK.md](docs/PARTICIPANT_RUNBOOK.md) — team dev loop, CLI reference, certification, troubleshooting
- [docs/PRODUCTION_NOTES.md](docs/PRODUCTION_NOTES.md) — topology, auth, known limits, migration path, observability

## License

MIT
