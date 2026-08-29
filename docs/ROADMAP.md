# Roadmap — Agent Lab

Principle from SPEC §30: **build vertically before horizontally.** Get Onboarding Agent → Device Agent → Markdown → tool → MockWorld → HITL → verification → eval completely working. Then Access/Systems/Applications are repetition of established primitives.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done

## Phase A — Vertical slice (MVP)

Goal: one employee, one coordinator, one domain agent, full loop with injected faults.

Deliverables:

- [ ] Repo scaffold per SPEC §28; uv workspaces; ruff/ty/pytest gates wired
- [ ] **SDK**: `protocols.py` (WorkflowRequest/Status/Outcome, event types), `transport.py` (`AgentTransport`/`AgentLabTransport`: send/subscribe/delegate/report_status), `knowledge.py` (`KnowledgeProvider`/`MarkdownKnowledgeProvider`: search/get_document), `client.py` (backend HTTP/WS client), `agent.py` (`TeamAgent` wrapper)
- [ ] **MockWorld**: schema.sql (SPEC §9 tables), seeds, FastAPI app; agent-facing `/world/*` APIs (SPEC §8); privileged `/simulation/*` APIs with enforced separation
- [ ] **Device tools** (SPEC §10) as ADK function tools calling MockWorld HTTP
- [ ] **Backend core**: workflow engine (SPEC §12 state machine), Case Store, Event Store, Channel Service (private chats + `#onboarding`), Agent Router (local agent registration), Human Task Service (SPEC §15)
- [ ] **Onboarding Agent** (SPEC §11): case creation, delegation, tracking, blocker reconciliation, escalation, readiness verification
- [ ] **Device Agent** + `knowledge/devices/` corpus (SPEC §6)
- [ ] **Scenario Engine**: YAML loading, reset, timed world mutations (SPEC §16), tool fault injection (SPEC §17.2)
- [ ] **Evaluation Engine**: deterministic assertions + scoring weights (SPEC §24), scenario result view (SPEC §25)
- [ ] **Certification**: Device 5-scenario pack (SPEC §18) + contract certification suite (SPEC §19)
- [ ] **Frontend MVP**: Agents, Channels, Cases, World, Trace, Human Tasks, Scenarios, Evals (SPEC §21-23)
- [ ] **CLI**: `agent-lab dev` local loop (SPEC §26)

Acceptance criteria: ONB-42 completes end-to-end in simulation with one injected fault; Device pack 5/5; contract certification passes; deterministic eval green; `agent-lab dev` works from a fresh clone of `templates/team-agent`.

## Phase B — Horizontal replication

- [ ] Access, Systems, Applications agents + tools + knowledge corpora
- [ ] Their 5-scenario certification packs (SPEC §18)
- [ ] Integration scenario: 5 employees + delayed device + access approval (SPEC §20)

Acceptance: all four domain agents pass packs + contract cert; integration scenario green.

## Phase C — Multi-agent finals

- [ ] Unknown scenario + chaos scenario (SPEC §20), platform-owned, in `scenarios/hidden/`
- [ ] "Are next Monday's new employees ready?" verdict exercised end-to-end

Acceptance: chaos scenario yields correct readiness verdict with full audit trail; hidden scenarios demonstrably absent from participant distributions.

## Phase D — Hardening & completeness

- [ ] World inspector reality-vs-belief diff view (SPEC §22)
- [ ] Full trace timeline UI; eval drill-down
- [ ] Concurrency: multiple simultaneous cases; WS reconnect; timeouts
- [ ] Scenario authoring guide; participant runbook; production migration notes (SPEC §30)

## Deferred (anticipate abstractions, don't implement — SPEC §29)

Slack transport · ConfluenceKnowledgeProvider · MCP domain tools · SSO · production IAM · Kubernetes/Kafka · advanced RAG · visual workflow builder · LLM-generated scenarios · production deployment.

## Production path (post-lab)

Scenario/evaluation machinery evolves into the certification environment: Build → domain scenarios → contract certification → multi-agent simulation → safety/policy eval → human review → deploy → production traces → failure → new regression scenario (SPEC §30).

## Estimated sessions

Phase A: ~6-8 Droid sessions (backend/SDK/mock-world) + 2 Codex sessions (frontend). Phases B-C: ~4-6 Droid sessions. Phase D: ~2-3.
