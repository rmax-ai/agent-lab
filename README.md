# Enterprise Agent Lab

Development, simulation, and evaluation environment for team-owned operational AI agents.

**Agent Lab** is a self-contained simulation harness around [Google ADK](https://google.github.io/adk-docs/). Teams receive an agent template, a Markdown knowledge base, typed tools backed by a shared MockWorld API, and certification scenarios. The initial business process is **monthly employee onboarding**: an Onboarding Agent coordinates Access, Device, Systems, and Applications domain agents against a simulated reality — then the whole system must answer, deterministically: *"Are next Monday's new employees ready?"*

```
Onboarding Agent → delegates outcomes → Domain Agents → reason via knowledge + observed state → Domain Tools → MockWorld
```

The coordinator owns the process. Domain agents own their workflows.

## Key properties

| Property | Hackathon | Later |
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

**Phase 3 — architecture, research, and design docs complete.** Implementation not started. Board: [GitHub issues](https://github.com/rmax-ai/agent-lab/issues). See [docs/ROADMAP.md](docs/ROADMAP.md).

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
