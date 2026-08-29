#!/usr/bin/env bash
# Agent Lab Phase 3 board creation. Idempotent-ish: run once.
set +e
R="rmax-ai/agent-lab"
mkdir -p /tmp/al-issues

# ---------------------------------------------------------------- Plan epic
PLAN_URL=$(gh issue create --repo "$R" \
  --title "[Plan] Agent Lab — Full Development" \
  --label "type:epic,status:ready" \
  --body-file /dev/stdin <<'BODY'
## Project Plan

Top-level epic tracking the Agent Lab development lifecycle. Ground truth: `SPEC.md` (30 sections). Board convention: labels ARE the columns (`status:*`, `phase:*`, `type:*`).

### Phases
- Phase 0: Scope & Architecture ✅
- Phase 1: Research ✅
- Phase 2: Supporting Files ✅
- Phase 3: GitHub Setup ✅
- Phase 4: Implementation (Roadmap Phase A: vertical slice)
- Phase 5: Verification & Closeout
- Phase 6: Website

### Implementation roadmap (docs/ROADMAP.md)
- **Phase A** (stories A.1–A.15): vertical slice — SDK, MockWorld, backend core, Onboarding + Device agents, scenario engine, deterministic eval, Device certification, CLI, frontend MVP.
- **Epic B:** Access/Systems/Applications replication + integration scenario.
- **Epic C:** final multi-agent simulations (unknown + chaos), readiness verdict.
- **Epic D:** world inspector, trace UI, concurrency hardening, runbooks.

### Acceptance Criteria
- [ ] All phases `status:done`
- [ ] Hard gates: ruff check → ty check → pytest (backend/SDK), frontend typecheck+tests
- [ ] validate-project-docs passes on fresh clone
- [ ] Website deployed and validated (Phase 6)

### Key references
- SPEC.md · docs/ARCHITECTURE.md · docs/THREAT_MODEL.md · DECISIONS.md · docs/ROADMAP.md · AGENTS.md
BODY
)
PLAN_NUM=$(echo "$PLAN_URL" | grep -oE '[0-9]+$')

# ---------------------------------------------------------------- Phase issues
P0_URL=$(gh issue create --repo "$R" \
  --title "[Phase 0] Scope & Architecture" --label "phase:0,status:done" \
  --body-file /dev/stdin <<'BODY'
## Phase 0: Scope & Architecture — DONE 2026-08-29

Received dense 30-section spec; extracted project identity; sanitized internal org names (FDE → Platform Team, Business Enablement → Enterprise Operations) for the public repo.

### Deliverables
- [x] SPEC.md — verbatim 30-section extraction (ground truth)
- [x] docs/ARCHITECTURE.md — 13 sections, planner subagent
- [x] docs/THREAT_MODEL.md — 10 threats × 7-field analysis + open questions
- [x] AGENTS.md — non-negotiables + gates
- [x] docs/ROADMAP.md — Phases A–D with acceptance criteria
- [x] README.md

### Decisions flagged at boundary
Repo `rmax-ai/agent-lab`, public, MIT — confirmed by Max 2026-08-29.
BODY
)
P0_NUM=$(echo "$P0_URL" | grep -oE '[0-9]+$')

P1_URL=$(gh issue create --repo "$R" \
  --title "[Phase 1] Research" --label "phase:1,status:done" \
  --body-file /dev/stdin <<'BODY'
## Phase 1: Research — DONE 2026-08-29

### Deliverables
- [x] docs/research/phase-1-findings.md — ADK v2 API shape (pre-researched reference, verified ≤2.3.0) + fresh PyPI version pins (google-adk 2.8.0, fastapi 0.141.1, pydantic 2.13.5, pytest 9.1.1, ruff 0.16.5, ty 0.0.75, …)
- [x] 4 re-verification items flagged for Phase A (ADK 2.8.0 API drift, WS serving mode, fault-injection seams, pytest 9 + pytest-asyncio 1.4 compat)
- [x] Injection-protection pattern adopted (DATA_DELIMITER for knowledge docs)

### Acceptance Criteria
- [x] Version pins from primary sources (PyPI JSON API)
- [x] Research informs DECISIONS.md (DEC-16 flat schemas, DEC-17 model pinning)
BODY
)
P1_NUM=$(echo "$P1_URL" | grep -oE '[0-9]+$')

P2_URL=$(gh issue create --repo "$R" \
  --title "[Phase 2] Supporting Files" --label "phase:2,status:done" \
  --body-file /dev/stdin <<'BODY'
## Phase 2: Supporting Files — DONE 2026-08-29

### Deliverables
- [x] AGENTS.md hub with companion-doc spokes
- [x] DECISIONS.md — 17 decisions (DEC-01…DEC-17), [FINAL]/[PROVISIONAL]/[SPEC] statuses
- [x] PYTHON_DEVELOPMENT.md, PYTHON_API_DESIGN.md, PYTHON_SYSTEM_DESIGN_PATTERNS.md, PYTHON_ARCHITECTURE.md
- [x] .gitignore (includes scenarios/hidden/ — DEC-14)
- [x] docs/ROADMAP.md (from Phase 0)

### Acceptance Criteria
- [x] AGENTS.md covers non-negotiables + tooling + testing + delegation conventions
- [x] Companion docs contain concrete syntax (Pydantic models, ADK runner patterns, event store helper)
BODY
)
P2_NUM=$(echo "$P2_URL" | grep -oE '[0-9]+$')

P3_URL=$(gh issue create --repo "$R" \
  --title "[Phase 3] GitHub Setup" --label "phase:3,status:done" \
  --body-file /dev/stdin <<'BODY'
## Phase 3: GitHub Setup — DONE 2026-08-29

### Deliverables
- [x] Repo created: rmax-ai/agent-lab (public, MIT), docs pushed
- [x] Labels: 6 status + 2 type + 7 phase + 9 area
- [x] SPEC.md at repo root (pushed)
- [x] Plan epic + phase issues 0–6 created
- [x] Phase A stories (A.1–A.15) + roadmap epics B/C/D created
- [x] Dependency comments linking stories

### Verification
- [x] `gh label list` shows 30 labels
- [x] Board queries: `gh issue list --repo rmax-ai/agent-lab --state open`
BODY
)
P3_NUM=$(echo "$P3_URL" | grep -oE '[0-9]+$')

P4_URL=$(gh issue create --repo "$R" \
  --title "[Phase 4] Implementation — Roadmap Phase A (vertical slice)" --label "phase:4,status:ready" \
  --body-file /dev/stdin <<'BODY'
## Phase 4: Implementation — Roadmap Phase A

### Objective
Build the vertical slice end-to-end (SPEC §30): Onboarding Agent → Device Agent → Markdown knowledge → ADK tools → MockWorld → HITL → verification → deterministic eval.

### Stories (A.1–A.15, see board)
Scaffold → SDK protocols → SDK knowledge/transport → MockWorld schema → MockWorld APIs → backend core → channels/WS → human tasks → Device agent → Onboarding coordinator → scenario engine → eval engine → certification packs → CLI → frontend MVP.

### Execution model (per Max's pipeline)
Plan → delegate to Droid (Python, `~/bin/isolated-agent`) / Codex (frontend) → sequential background sessions → Hermes review. PR gates: `uv run ruff check && uv run ty check && uv run pytest`. ADK 2.8.0 re-verification items from docs/research/phase-1-findings.md MUST be checked first (story A.2/A.3 blocked on this).

### Acceptance Criteria
- [ ] ONB-42 completes end-to-end in simulation with one injected fault
- [ ] Device certification pack 5/5
- [ ] Contract certification suite passes (SPEC §19)
- [ ] Deterministic eval green
- [ ] `agent-lab dev` works from fresh clone of templates/team-agent
- [ ] All stories `status:done`, zero open stories
BODY
)
P4_NUM=$(echo "$P4_URL" | grep -oE '[0-9]+$')

P5_URL=$(gh issue create --repo "$R" \
  --title "[Phase 5] Verification & Closeout" --label "phase:5,status:backlog" \
  --body-file /dev/stdin <<'BODY'
## Phase 5: Verification & Closeout

### Objective
- [ ] Hard Gate 1: ruff check → ty check → pytest (stop on first failure)
- [ ] Hard Gate 2: validate-project-docs (fresh clone, claims audit, cross-reference)
- [ ] Close Plan epic, tag release v0.1.0
- [ ] Baseline any test failures before claiming regressions

### Reference
Skill: fp-phase-5-verification
BODY
)
P5_NUM=$(echo "$P5_URL" | grep -oE '[0-9]+$')

P6_URL=$(gh issue create --repo "$R" \
  --title "[Phase 6] Website" --label "phase:6,status:backlog" \
  --body-file /dev/stdin <<'BODY'
## Phase 6: Website

### Objective
Single-page static landing page (SvelteKit 5 or project-standard stack), deployed to rmax-ai.github.io/agent-lab.

### Acceptance Criteria
- [ ] All content verified against actual project docs — NO LLM-hallucinated content
- [ ] Line-by-line audit before merge (replace all LLM-generated copy with verified text)
- [ ] All links resolve; dark theme consistent; GitHub Pages serving

### Reference
Skill: fp-phase-6-website
BODY
)
P6_NUM=$(echo "$P6_URL" | grep -oE '[0-9]+$')

# ---------------------------------------------------------------- Roadmap epics
EB_URL=$(gh issue create --repo "$R" \
  --title "[Epic B] Horizontal Replication — Access, Systems, Applications" \
  --label "type:epic,phase:4,status:backlog" \
  --body-file /dev/stdin <<'BODY'
## Epic B: Horizontal Replication

After the vertical slice passes (Phase A), replicate established primitives (SPEC §30).

### Scope
- Access/Systems/Applications agents + tools (SPEC §10) + knowledge corpora
- Their 5-scenario certification packs (SPEC §18)
- Integration scenario: 5 employees + delayed device + access approval (SPEC §20)

### AC
All four domain agents pass packs + contract cert; integration scenario green.
BODY
)
EB_NUM=$(echo "$EB_URL" | grep -oE '[0-9]+$')

EC_URL=$(gh issue create --repo "$R" \
  --title "[Epic C] Multi-Agent Finals — Integration, Unknown, Chaos" \
  --label "type:epic,phase:4,status:backlog" \
  --body-file /dev/stdin <<'BODY'
## Epic C: Multi-Agent Finals

Platform-owned hidden scenarios (SPEC §20). Never distributed to participants (DEC-14).

### Scope
- Unknown scenario + chaos scenario (12 starters, inventory exhaustion, unanswered + unauthorized approvals, lying provisioning, knowledge conflict, tool timeout)
- "Are next Monday's new employees ready?" verdict with full audit trail

### AC
Chaos scenario yields correct readiness verdict; hidden scenarios demonstrably absent from participant distributions.
BODY
)
EC_NUM=$(echo "$EC_URL" | grep -oE '[0-9]+$')

ED_URL=$(gh issue create --repo "$R" \
  --title "[Epic D] Hardening & UI Completeness" \
  --label "type:epic,phase:4,status:backlog" \
  --body-file /dev/stdin <<'BODY'
## Epic D: Hardening & UI Completeness

### Scope
- World inspector reality-vs-belief diff view (SPEC §22)
- Full trace timeline UI + eval drill-down
- Concurrency: multiple simultaneous cases, WS reconnect, timeouts
- Scenario authoring guide, participant runbook, production migration notes (SPEC §30)
BODY
)
ED_NUM=$(echo "$ED_URL" | grep -oE '[0-9]+$')

# ---------------------------------------------------------------- Phase A stories
mkstory() {
  local title="$1" labels="$2" body="$3" num
  num=$(gh issue create --repo "$R" --title "$title" --label "$labels" --body "$body" | grep -oE '[0-9]+$')
  echo "STORY $title → #$num"
}

mkstory "[Story A.1] Workspace scaffold — uv monorepo + gates" \
  "type:story,phase:4,area:backend,status:backlog" \
  "## Context
DEC-03: uv workspace monorepo per SPEC §28 layout. This story unblocks everything else — deterministic infrastructure, done directly (not delegated).

## Deliverables
- Root pyproject.toml (workspace) with members backend/, sdk/, mock-world/, agents/onboarding/
- Pinned deps per docs/research/phase-1-findings.md
- ruff config (line-length 100, project rules), ty config, pytest config (pytest-asyncio asyncio_mode=auto)
- .gitignore present (already committed)
- CI workflow (GitHub Actions: ruff + ty + pytest on push/PR)

## AC
uv sync at root succeeds; uv run ruff check, uv run ty check, uv run pytest all green on empty package stubs.
## Reference
PYTHON_DEVELOPMENT.md, PYTHON_ARCHITECTURE.md"

mkstory "[Story A.2] SDK protocols — workflow contract, events, enums" \
  "type:story,phase:4,area:sdk,status:backlog" \
  "## Context
SPEC §12/§23. Flat Pydantic v2 models (DEC-16 — no nested model-of-model lists). Field names verbatim from SPEC.

## Deliverables
- WorkflowRequest / WorkflowStatus / WorkflowOutcome / Blocker / Event / HumanTask models
- WorkflowState enum (ACKNOWLEDGED RUNNING BLOCKED WAITING_FOR_HUMAN FAILED COMPLETED)
- HumanTaskType enum (APPROVAL MISSING_INFORMATION CONFLICT_RESOLUTION EXCEPTION_HANDLING MANUAL_ACTION)
- Event type vocabulary (SPEC §23)
- Unit tests incl. state machine serialization round-trips

## AC
Importable as agentlab.sdk.protocols; JSON (de)serialization matches SPEC §12 examples exactly.
## Reference
PYTHON_API_DESIGN.md. BLOCKED-FIRST: verify google-adk 2.8.0 API drift items from docs/research/phase-1-findings.md §2 before A.3."

mkstory "[Story A.3] SDK knowledge + transport — ABCs and first adapters" \
  "type:story,phase:4,area:sdk,status:backlog" \
  "## Context
SPEC §4/§6/§14 — the swappable boundaries, the core engineering property.

## Deliverables
- KnowledgeProvider ABC + MarkdownKnowledgeProvider (search(query)/get_document(id), frontmatter parsing, DATA_DELIMITER wrapping per DEC-11)
- AgentTransport ABC (send/subscribe/delegate/report_status) + AgentLabTransport (WebSocket client + HTTP fallback)
- agentlab.sdk.client — backend HTTP client (cases, tasks, events)
- TeamAgent wrapper (SPEC §5: id, goal, instructions, knowledge, tools) with ADK 2.8.0 live-mode pattern (auto_create_session=True; Content/Part; event.partial filtering)
- Deterministic tests via before_model_callback mock (no real LLM)

## AC
TeamAgent boots against a scripted backend; knowledge docs load with delimiter; transport ABCs import cleanly with zero backend imports (dependency direction per PYTHON_ARCHITECTURE.md).
## Depends on
A.2"

mkstory "[Story A.4] MockWorld schema + seeds" \
  "type:story,phase:4,area:mock-world,status:backlog" \
  "## Context
SPEC §7/§9. SQLModel + SQLite, WAL (DEC-02). Tables verbatim from SPEC §9: employees, managers, inventory, devices, device_orders, identities, groups, entitlements, access_requests, systems, system_accounts, applications, application_access, onboarding_cases, workflow_runs, human_tasks, events.

## Deliverables
- schema.sql + SQLModel model layer
- Seed data: employees (E42 etc.), managers, inventory (macbook_pro_14, macbook_air_15), device policies, entitlements baseline
- Migration/init path (create_all + seeds on boot)

## AC
Fresh DB initializes with canonical seed state; seed matches SPEC §16 initial_state vocabulary (dot-path mutation targets).
## Reference
PYTHON_ARCHITECTURE.md (world opened only by agentlab.world)"

mkstory "[Story A.5] MockWorld APIs — agent-facing + privileged simulation" \
  "type:story,phase:4,area:mock-world,status:backlog" \
  "## Context
SPEC §8. Two route groups, two trust tiers. DEC-07 (per-domain tool identity), DEC-09 (bearer token for /simulation/*, per-agent registration tokens).

## Deliverables
- Agent-facing /world/* endpoints (verbatim from SPEC §8) with Pydantic response models
- Privileged /simulation/* endpoints: reset/load/mutate/faults/events
- Server-side domain enforcement: caller agent identity → allowed endpoint map (Device agent cannot hit /world/access/*)
- 409 → Blocker envelope for business rejections (e.g. reserve unavailable device)
- Route tests with httpx AsyncClient

## AC
/simulation/* unreachable without token; cross-domain call rejected; mutation by dot-path (inventory.macbook_pro_14.available) works.
## Depends on
A.4"

mkstory "[Story A.6] Backend core — workflow engine, case store, event store" \
  "type:story,phase:4,area:backend,status:backlog" \
  "## Context
SPEC §11/§12/§23. State machine transition table (PYTHON_SYSTEM_DESIGN_PATTERNS §1), append-only events, case_id correlation everywhere (DEC-06).

## Deliverables
- Workflow engine: transition validation, atomic event+row writes, COMPLETED requires verified=true
- Case store: onboarding_cases CRUD, readiness aggregation
- Event store: emit_event helper + trace query
- Blockers with codes (NO_INVENTORY etc.)

## AC
Illegal transitions rejected + audited; full SPEC §23 trace replayable from events table.
## Depends on
A.2"

mkstory "[Story A.7] Channel service + WebSocket hub + agent router" \
  "type:story,phase:4,area:backend,status:backlog" \
  "## Context
SPEC §13/§14/§26. Channels (#onboarding, #devices, …) + private agent chats; agents register locally; NL visible, structured events underneath.

## Deliverables
- WS hub: connection per agent keyed by agent identity, heartbeat, reconnect
- Channel pub/sub with persistence; private agent:<id> channels
- Agent Router: registration endpoint, status (ONLINE), startup contract output (SPEC §26)
- Human participant sockets (UI)

## AC
Two scripted agents exchange messages on #onboarding; message history survives reconnect; startup prints the SPEC §26 ✓ block.
## Depends on
A.3, A.6"

mkstory "[Story A.8] Human task service" \
  "type:story,phase:4,area:backend,status:backlog" \
  "## Context
SPEC §15. HITL is first-class persisted state. DEC-08 (300s no-response → escalate), DEC-10 (requested_from authorization).

## Deliverables
- HumanTask model + persistence (fields verbatim from SPEC §15)
- Task lifecycle: OPEN → decision (approve/reject per allowed_actions) → event → workflow resume hook
- API for UI + for simulated human actors (scenario injection, SPEC §17.4)
- No-response SLA timer → escalation event

## AC
Approval flows: task created → workflow WAITING_FOR_HUMAN → decision event → workflow RUNNING; unauthorized resolver rejected (DEC-10).
## Depends on
A.6"

mkstory "[Story A.9] Device agent + knowledge corpus" \
  "type:story,phase:4,area:agents,status:backlog" \
  "## Context
SPEC §5/§6/§10. First domain agent — the vertical slice template.

## Deliverables
- agents/device/ layout: agent.py, instructions.md, knowledge/*.md (standard-device-policy, location-policy, inventory-substitution, replacements, escalation, exceptions with frontmatter), tools/device.py, scenarios/ placeholders
- 6 Device ADK function tools (SPEC §10) calling MockWorld HTTP
- Knowledge loaded with DATA_DELIMITER (DEC-11); substitution policy example verbatim from SPEC §6
- templates/team-agent/ scaffold generated from this layout

## AC
Device agent passes a scripted happy-path workflow (mock model or live Gemini via env key); tools return structured results; no direct SQLite access.
## Depends on
A.2, A.3, A.5"

mkstory "[Story A.10] Onboarding coordinator agent" \
  "type:story,phase:4,area:agents,status:backlog" \
  "## Context
SPEC §11. Owns employee_onboarding_ready; delegates OUTCOMES, never domain actions. Depth limit 2 (DEC-08).

## Deliverables
- agents/onboarding/: case creation → required-workflow determination → delegation → progress tracking → blocker reconciliation → dependency coordination → human intervention requests → escalation → readiness verification
- Verdict logic: are Monday's starters ready? (aggregate workflow outcomes + blockers)

## AC
Coordinator drives a scripted 2-agent onboarding (device + stub access) to readiness verdict; delegation only of goals (WorkflowRequest), zero domain tool calls.
## Depends on
A.6, A.7, A.8"

mkstory "[Story A.11] Scenario engine — YAML loader, timed mutations, fault injection" \
  "type:story,phase:4,area:scenarios,status:backlog" \
  "## Context
SPEC §16/§17. Scenarios control the world, never the agents. DEC-05 (faults only on mutation tools).

## Deliverables
- YAML schema: id, initial_state, events (at: N, mutate dot-path), expected (required_events, allowed_final_states, forbidden_events)
- Timer scheduler (asyncio) firing /simulation/mutate
- Fault injector via ADK before/after_tool_callback: timeout, 500, stale, success_without_state_change
- /simulation/reset + load

## AC
device-inventory-exhausted scenario runs: inventory drops at t=30 without telling the agent; agent discovers via check_inventory.
## Depends on
A.5, A.6"

mkstory "[Story A.12] Evaluation engine — deterministic assertions + weights" \
  "type:story,phase:4,area:eval,status:backlog" \
  "## Context
SPEC §24/§25. Deterministic primary; LLM-as-judge never pass/fail. Weights 35/25/20/15/5.

## Deliverables
- Assertion kinds: state, safety invariants, trajectory (required_events), system behavior (no infinite delegation, no excessive retries, no case contamination, no premature completion)
- Scoring with SPEC §24 weights; per-scenario PASS/FAIL table + Expected-vs-Observed detail (SPEC §25)
- pytest integration (one param per scenario YAML)

## AC
Scripted good and bad runs produce correct scores; failed scenario shows the SPEC §25 diff view.
## Depends on
A.2, A.6"

mkstory "[Story A.13] Device certification pack + contract certification suite" \
  "type:story,phase:4,area:scenarios,status:backlog" \
  "## Context
SPEC §18/§19. The gate to the final simulation.

## Deliverables
- scenarios/devices/: 01_happy_path, 02_missing_location, 03_no_inventory, 04_delivery_failure, 05_replacement_requires_approval (YAML)
- Contract certification suite (SPEC §19 checklist) as pytest: accepts request, acknowledges, RUNNING, blockers, HumanTask when appropriate, resumes, verifies, COMPLETED, case_id correlation

## AC
Device agent passes 5/5 pack + contract cert in CI (scripted model).
## Depends on
A.9, A.11, A.12"

mkstory "[Story A.14] CLI — agent-lab dev" \
  "type:story,phase:4,area:cli,status:backlog" \
  "## Context
SPEC §26. The dev loop is the product experience: edit → run → scenario → inspect trace → improve.

## Deliverables
- agent-lab CLI (typer): dev (boot local agent + connect WS), scenario run, trace, status
- Startup output exactly per SPEC §26 (✓ connected, MockWorld available, knowledge loaded: N, tools registered: N, ONLINE)
- Templates copy/init command for new teams

## AC
Fresh clone of templates/team-agent + uv sync + agent-lab dev → SPEC §26 output; scenario run end-to-end with trace output.
## Depends on
A.7, A.9"

mkstory "[Story A.15] Frontend MVP — 8 views" \
  "type:story,phase:4,area:frontend,status:backlog" \
  "## Context
SPEC §21/§22/§23/§25. React/Vite. Codex territory (TypeScript).

## Deliverables
- Views: Agents (status list), Channels (chat), Cases (ONB-42 style panel with per-domain ✓/!/…), World (inspector, human-privileged), Trace (timeline), Human Tasks (approve/reject), Scenarios (results table), Evals (scores)
- WS + HTTP client wiring to backend routes

## AC
All 8 views render against a live backend during an ONB-42 simulation; approval action flows through Human Task service.
## Depends on
A.7, A.8, A.11, A.12"

echo "=== BOARD MAP ==="
echo "PLAN #$PLAN_NUM"
echo "P0 #$P0_NUM  P1 #$P1_NUM  P2 #$P2_NUM  P3 #$P3_NUM  P4 #$P4_NUM  P5 #$P5_NUM  P6 #$P6_NUM"
echo "EPIC_B #$EB_NUM  EPIC_C #$EC_NUM  EPIC_D #$ED_NUM"

# ---------------------------------------------------------------- Dependency comments
gh issue comment "$P4_NUM" --repo "$R" --body "**Blocked on ADK re-verification:** docs/research/phase-1-findings.md §2 (google-adk 2.8.0 API drift) must be checked before stories A.2/A.3 proceed."
gh issue comment "$P5_NUM" --repo "$R" --body "**Depends on:** #$P4_NUM"
gh issue comment "$P6_NUM" --repo "$R" --body "**Depends on:** #$P5_NUM"
gh issue comment "$EB_NUM" --repo "$R" --body "**Depends on:** Phase A stories complete (#$P4_NUM)"
gh issue comment "$EC_NUM" --repo "$R" --body "**Depends on:** #$EB_NUM"
gh issue comment "$ED_NUM" --repo "$R" --body "**Depends on:** #$EC_NUM"
gh issue comment "$PLAN_NUM" --repo "$R" --body "Phase issues: #$P0_NUM #$P1_NUM #$P2_NUM #$P3_NUM #$P4_NUM #$P5_NUM #$P6_NUM · Roadmap epics: #$EB_NUM #$EC_NUM #$ED_NUM"
echo "=== DONE ==="
