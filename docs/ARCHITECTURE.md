# Agent Lab — Architecture

> Status: Draft · Derived from [SPEC.md](../SPEC.md) (authoritative ground truth; section references like §3 point there)
> Scope: hackathon/MVP architecture as specified. Deferred items (Slack, Confluence, MCP, SSO, production IAM, Kubernetes, Kafka, advanced RAG, production deployment — §29) are anticipated as abstractions but not implemented.

## 1. Executive summary

Agent Lab is a development, simulation, and evaluation environment for team-owned operational AI agents. The initial business process is **monthly employee onboarding** (§1).

The recommendation is to make Agent Lab a **self-contained simulation harness around Google ADK**: each team receives an agent template, a Markdown knowledge base, typed tools backed by a shared MockWorld API, and certification scenarios. The Platform Team owns the coordinator, simulation engine, shared UI, contracts, and evaluation harness. Every boundary is kept swappable — Markdown → Confluence, MockWorld → real enterprise APIs/MCP, Lab channels → Slack — so the hackathon tests the architecture that could eventually be productionized without requiring production infrastructure during the offsite.

Participating teams and owned outcomes (§1):

| Team | Agent | Owned outcome |
|---|---|---|
| Enterprise Access Management | Access Agent | `employee_access_ready` |
| Enterprise Device Management | Device Agent | `employee_device_ready` |
| Enterprise Systems Administrators | Systems Agent | `employee_systems_ready` |
| Enterprise Applications Engineering | Applications Agent | `employee_applications_ready` |
| Platform Team | Onboarding Agent | `employee_onboarding_ready` |

The architectural principle (§1):

```
Onboarding Agent
      │
      │ delegates business outcomes
      ▼
Domain Agents
      │
      │ reason using knowledge + observed state
      ▼
Domain Tools
      │
      ▼
MockWorld
```

**The coordinator owns the process. Domain agents own their workflows.**

Three engineering properties define the design:

1. **Boundary swappability.** Agents depend on abstractions, not adapters: `KnowledgeProvider` (MarkdownKnowledgeProvider hackathon → ConfluenceKnowledgeProvider later), `DomainTool` (MockWorld implementation hackathon → Direct API later → MCP optional later), `AgentTransport` (AgentLabTransport hackathon → SlackTransport later). Agents should not need significant changes when these adapters change. **This is probably the most important engineering property of the lab.** (§4)
2. **MockWorld is the only external reality.** Agents reach it only through their own ADK function tools; SQLite is never exposed directly to agents (§8, §9).
3. **Deterministic evaluation is primary.** State assertions, safety invariants, trajectory assertions, and system-behavior checks — LLM-as-judge is explicitly not the main pass/fail mechanism (§24).

Everything can initially run on one shared machine except team agents, which can run on participants' laptops (§3). The final exercise asks something deliberately simple: **"Are next Monday's new employees ready?"** The system must determine the answer (§2, §20). The durable output is an initial Agent Lab + agent contract + assurance framework that the Platform Team can own and extend to other workflows (§30).

## 2. Architecture overview

System diagram from §3:

```
┌──────────────────────────────────────────────────────────────┐
│                       AGENT LAB UI                           │
│                                                              │
│  Agents │ Channels │ Cases │ World │ Human Tasks │ Evals    │
└─────────────────────────────┬────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                    AGENT LAB BACKEND                         │
│                                                              │
│  Agent Router              Case Store                        │
│  Channel Service           Event Store                       │
│  Scenario Engine           Evaluation Engine                 │
│  Human Task Service        MockWorld API                     │
└───────────┬───────────────────────────────┬──────────────────┘
            │                               │
            │ Agent protocol                │ Business APIs
            │                               │
   ┌────────┴────────┐                 ┌────┴─────┐
   │                 │                 │ SQLite   │
   ▼                 ▼                 └──────────┘
Onboarding Agent    Team Agents
Google ADK          Google ADK
                       │
              ┌────────┴────────┐
              │                 │
        Markdown KB        ADK Tools
```

Reading the diagram top to bottom:

- **Agent Lab UI** (React/Vite, §27) exposes six areas — Agents | Channels | Cases | World | Human Tasks | Evals — plus additional tabs: Case, World, Trace, Human Tasks, Scenarios, Evals (§21).
- **Agent Lab Backend** is one FastAPI process containing eight components: Agent Router, Channel Service, Scenario Engine, Evaluation Engine, Human Task Service, Case Store, Event Store, MockWorld API (§3, §27).
- The backend talks to the agents over the **Agent protocol** (WebSocket + HTTP, §27): Onboarding Agent and Team Agents, all Google ADK. Team agents carry a **Markdown KB** and **ADK Tools**.
- The backend talks to **SQLite** over **Business APIs** — MockWorld, the simulated external reality. MockWorld covers HR, IAM, Devices, and Systems/Apps in one FastAPI application; no separate microservices (§7).

The MVP rule for the whole system: **do not introduce distributed infrastructure yet** (§27).

## 3. Component architecture

All eight backend components run inside one backend process (§27), alongside the SDK that agents link against (`sdk/{agent.py, client.py, transport.py, knowledge.py, protocols.py}`, §28).

### Agent Router

Routes agent-protocol traffic (WebSocket + HTTP, §27) between the UI, the Onboarding Agent, and the registered Team Agents. Supports **local agent registration** — the MVP must-have that lets each team's agent on a participant laptop join the shared environment (§26, §29). The Router is the enforcement point for the workflow contract (§12) and for correlation: everything is correlated with `case_id` (§19).

### Channel Service

Implements Agent Lab channels instead of Slack initially: `#onboarding`, `#access`, `#devices`, `#systems`, `#applications`, plus private agent conversations. Humans and agents can participate (§13). Natural-language communication is visible; structured events travel underneath (§13). The Channel Service sits behind the `AgentTransport` abstraction (`send()`, `subscribe()`, `delegate()`, `report_status()`); hackathon implementation is `AgentLabTransport`, future implementation is `SlackTransport` — moving to Slack doesn't require redesigning the agents (§14).

### Case Store

Persists `onboarding_cases` (§9) — e.g., `ONB-42` — and the per-case rollup shown in the UI (Access ✓, Device !, Systems …, Apps ✓, Blockers: 1, Approvals: 1, §21). `case_id` is the correlation key threaded through every workflow, human task, event, and message (§19). The Onboarding Agent creates cases and owns their lifecycle (§11).

### Event Store

Append-only record of **observable actions rather than private model reasoning** (§23) — `CASE_CREATED`, `WORKFLOW_DELEGATED`, `TOOL_CALL`, `TOOL_RESULT`, `KNOWLEDGE_READ`, `BLOCKER_CREATED`, `HUMAN_TASK_CREATED`, `APPROVAL_GRANTED`, `OUTCOME_VERIFIED`, etc. Persisted in the `events` table (§9). It is the foundation for debugging and evaluation (§23): scenario `expected` blocks reference event names (§16), and the Trace tab renders the timeline (§21).

### Scenario Engine

Loads YAML scenario definitions (`id`, `initial_state`, `events`, `expected` — §16) and **plays reality** through the privileged simulation APIs (`/simulation/reset|load|mutate|faults|events`, §8). A scenario **controls the world, not the agents** (§16): the agent never receives "MacBooks have run out" — it discovers `check_inventory()` → `available = 0` (§16). Four injection mechanisms (§17): world-state mutation, tool fault, knowledge condition, human behavior. Platform-owned hidden scenarios live under `scenarios/hidden/` and must not be distributed to participants (§28).

### Evaluation Engine

Runs the deterministic evaluation model (§24): state assertions, safety invariants, trajectory assertions, system behavior — scored with the §24 weights. Produces per-scenario results (PASS/FAIL table, Expected vs Observed, final state, trace link — §25). Implemented as pytest + scenario evaluator (§27). LLM-as-judge is avoided as the main pass/fail mechanism (§24). Contract certification (§19) is the gate for joining the final simulation.

### Human Task Service

Human-in-the-loop as **first-class state, not chat improvisation** (§15). Persists HumanTask records (`human_tasks` table, §9) with the §15 fields, drives workflow status `WAITING_FOR_HUMAN`, and implements the resume loop: human decision → event → coordinator resumes workflow (§15). Handles the five task types and the human-behavior injections (approval, rejection, no response, ambiguous response, unauthorized approval — §17.4).

### MockWorld API

The simulated external reality: a shared API backed by SQLite covering HR, IAM, Devices, Systems/Apps in one FastAPI application (§7). Two route groups with a hard separation (§8): **agent-facing business APIs** (`/world/*` — agents access these ONLY through ADK tools) and **privileged simulation APIs** (`/simulation/*` — agents cannot access; the scenario engine uses them to play reality). SQLite is never exposed directly to agents (§9). See §11 for the full surface.

### SDK layer (what agents actually see)

`protocols.py` (WorkflowRequest/Status/Outcome, event types), `transport.py` (`AgentTransport`/`AgentLabTransport`), `knowledge.py` (`KnowledgeProvider`/`MarkdownKnowledgeProvider`), `client.py` (backend client), `agent.py` (the `TeamAgent` wrapper that handles the Agent Lab plumbing; teams modify `agent.py`, `instructions.md`, `knowledge/*.md`, `tools/`, `scenarios/` only — §5).

## 4. Request lifecycle

An onboarding case end-to-end: case creation → delegation → workflow contract → HITL → verification. The spine is the trace timeline (§23); the roles are the Onboarding Agent (coordinator) and a domain agent (here: Device).

**1. Case creation.** The Onboarding Agent creates a case (`CASE_CREATED` — `ONB-42`) and determines the required workflows (§11).

**2. Delegation of outcomes.** The Onboarding Agent delegates **business outcomes, not domain actions** (§11): Device Agent → `employee_device_ready`, Access Agent → `employee_access_ready`, Systems Agent → `employee_systems_ready`, Applications Agent → `employee_applications_ready`. It does **not** delegate `reserve_macbook()` or `add_group("risk")` — domain implementation remains encapsulated. On the trace:

```
10:31:02 Onboarding → Device   WORKFLOW_DELEGATED
```

**3. Workflow contract.** The domain agent accepts a `WorkflowRequest` (§12), acknowledges ownership, and reports RUNNING (§19):

```json
{
  "workflow_id": "WF-D-42",
  "case_id": "ONB-42",
  "goal": "employee_device_ready",
  "employee_id": "E42",
  "context": { "start_date": "2026-09-07" }
}
```

**4. Reasoning from knowledge + observed state.** The agent reads its Markdown knowledge and probes MockWorld through its own ADK tools. Perceptions come from tools, never from a narrator:

```
10:31:04 Device                TOOL_CALL check_inventory
10:31:04 MockWorld             TOOL_RESULT available=0
10:31:07 Device                KNOWLEDGE_READ substitution-policy.md
```

The agent never receives: "MacBooks have run out." It discovers: `check_inventory()` → `available = 0` (§16).

**5. Blocker and human-in-the-loop.** The agent reports blockers and creates a HumanTask when appropriate (§19), moving to `BLOCKED` / `WAITING_FOR_HUMAN` (§12, §15):

```json
{
  "workflow_id": "WF-D-42",
  "status": "blocked",
  "blockers": [{ "code": "NO_INVENTORY", "description": "Standard device unavailable" }]
}
```

```
10:31:10 Device                BLOCKER_CREATED
10:31:11 Device                HUMAN_TASK_CREATED
```

**6. Human decision resumes the workflow.** `WAITING_FOR_HUMAN` → human decision → event → coordinator resumes workflow (§15):

```
10:32:15 Human                 APPROVAL_GRANTED
10:32:17 Device                TOOL_CALL reserve_device
```

**7. Verification and completion.** The domain agent verifies the outcome and reports COMPLETED (§19):

```
10:32:20 Device                OUTCOME_VERIFIED
```

```json
{ "workflow_id": "WF-D-42", "status": "completed", "verified": true }
```

**8. Coordinator reconciliation.** Meanwhile the Onboarding Agent tracks progress, reconciles blockers, coordinates dependencies, requests human intervention, and escalates stalled work (§11) — e.g., announcing "ONB-42 is now AT_RISK. Human intervention requested." on `#onboarding` (§13). When all delegated outcomes are verified, it verifies overall readiness: `employee_onboarding_ready` (§11) — the substrate for answering **"Are next Monday's new employees ready?"** (§20).

Every step is correlated with `case_id` (§19); every step emits a structured event underneath the natural-language chatter (§13, §23). Passing the contract certification checklist (§19) is the gate for joining the final multi-agent simulation.

## 5. Trust boundaries

| # | Boundary | What crosses it | Enforcement per spec |
|---|---|---|---|
| 1 | Agent-facing vs simulation APIs | `/world/*` business calls (through ADK tools only); `/simulation/*` is the scenario engine's private playground | "Separate normal business APIs from privileged simulation APIs" (§8). "Agents access these only through ADK tools" (§8). "Simulation APIs — agents cannot access" (§8). Enforced by design: separate route groups and **no tool wrappers** for `/simulation/*`; asserted in tests (repo non-negotiable). "The scenario engine effectively plays reality" (§8) |
| 2 | Agents vs MockWorld SQLite | Nothing — agents never touch the database | "**Do not expose SQLite directly to agents.**" (§9). MockWorld is the only external reality; agents reach it only through their ADK function tools (§10, repo non-negotiable) |
| 3 | Teams vs platform | Team agents register locally and join the shared environment; teams see their own knowledge, tools, and certification scenarios; the Platform Team owns the coordinator, simulation engine, shared UI, contracts, and evaluation harness | Platform Team owns platform + starter templates (§1); "Each domain agent gets only its own tools" (§10); each team gets its own `knowledge/{team}/` directory (§6); hidden scenarios "should obviously not be distributed to participants" (§28); scenario results are shared (PASS/FAIL + Expected vs Observed + trace, §25) |
| 4 | Humans vs agents | Humans chat in channels and act on human tasks; agents observe the world only through tools | Humans and agents both participate in channels (§13); HumanTask records `requested_by`, `requested_from`, `resolved_by` (§15); the world inspector is a human view — "Agents do not have this privileged view" (§22); "unauthorized approval" is a tested injection (§17.4) |
| 5 | Domain isolation | Each domain agent reasons within its own knowledge corpus and tool set | Per-domain knowledge directories (§6); per-domain tool sets (§10); per-domain channels plus private conversations (§13); Onboarding Agent delegates outcomes, not domain actions, so "domain implementation remains encapsulated" (§11) |

Two properties follow from these boundaries and are treated as invariants:

- **MockWorld is the only external reality.** No other source of world state exists for agents.
- **The scenario engine effectively plays reality.** All faults, mutations, and knowledge conditions enter through the scenario engine's privileged mechanisms (§8, §16, §17).

Note: the spec defines **no credential or authentication mechanism** for the hackathon — SSO and production IAM are explicitly deferred (§29). Boundary enforcement rests on design separation (route groups, no tool wrappers, no SQLite exposure) plus evaluation, not on credentials. See THREAT_MODEL.md and §13 open questions.

## 6. Policy model

Policies live in the Markdown knowledge layer — there is no separate policy store in the spec.

- Each team gets its own directory: `knowledge/{access, devices, systems, applications, onboarding}/` (§6).
- Documents use **simple frontmatter where useful** (§6):

```markdown
---
title: Device substitution policy
owner: Enterprise Device Management
status: active
updated: 2026-08-20
---
```

- The frontmatter dimensions (`title`, `owner`, `status`, `updated`) are exactly what the scenarios probe: **retrieval; policy interpretation; conflicting documents; outdated documents; exceptions; missing knowledge** (§6). An `owner` field ties policy to the owning Enterprise Operations team; a `status`/`updated` pair enables staleness and conflict to be detected.

Example policy (substitution), verbatim from §6:

```markdown
# Device substitution
Engineering employees normally receive a MacBook Pro 14.
If unavailable, a MacBook Air 15 may be assigned with
manager approval.
A substitute must be confirmed before the employee's
start date.
```

- **Approvals as policy consequences.** Policies that require authorization surface as human-in-the-loop tasks of type `APPROVAL` (§15). In the example above, the policy requirement is "manager approval"; the HITL card presents the decision to a human (§15):

```
Device substitution required
MacBook Pro: unavailable
MacBook Air: 7 available
Policy requires manager approval.
[ Approve ]      [ Reject ]
```

- **Knowledge conditions as injections.** The scenario engine tests policy reasoning through knowledge-condition injection: outdated document, conflicting policy, missing policy, ambiguous exception (§17.3). For the MVP this is simply scenario-specific Markdown directories (§17.3). The expected/forbidden event blocks (§16) and safety invariants such as "no privileged access without approval" (§24) hold the agent to the policy.

Open question: the spec names "manager approval" as the policy requirement but does not specify how approver identity/authority is verified (see §13; the chaos scenario includes "another approval comes from unauthorized person", §20).

## 7. Workflow engine and workflow contract state machine

Every team agent supports the same conceptual lifecycle (§12):

```
WorkflowRequest
      ↓
ACKNOWLEDGED
      ↓
RUNNING
      ├── BLOCKED
      ├── WAITING_FOR_HUMAN
      ├── FAILED
      └── COMPLETED
```

| State | Meaning (per spec) | Transitions implied by spec |
|---|---|---|
| (initial) | WorkflowRequest issued — `{workflow_id, case_id, goal, employee_id, context}` (§12) | → ACKNOWLEDGED on accept |
| `ACKNOWLEDGED` | Agent accepts the WorkflowRequest and acknowledges ownership (§19) | → RUNNING |
| `RUNNING` | Agent reports RUNNING and executes (§19) | → BLOCKED / WAITING_FOR_HUMAN / FAILED / COMPLETED |
| `BLOCKED` | Agent reports blockers: `{code, description}` (§12) | resumed/reconciled by coordinator (§11) — exact transition rules TBD (not enumerated in spec) |
| `WAITING_FOR_HUMAN` | HumanTask open; resume loop: human decision → event → coordinator resumes workflow (§15) | → RUNNING on resume |
| `FAILED` | Terminal failure | retry semantics TBD (not specified) |
| `COMPLETED` | Outcome reported: `{workflow_id, status, verified}` (§12); requires the agent to have verified the outcome (§19) | terminal |

Status and outcome payloads, verbatim from §12:

```json
{
  "workflow_id": "WF-D-42",
  "status": "blocked",
  "blockers": [{ "code": "NO_INVENTORY", "description": "Standard device unavailable" }]
}
```

```json
{
  "workflow_id": "WF-D-42",
  "status": "completed",
  "verified": true
}
```

The workflow engine enforces the contract via **contract certification** (§19) — every agent must demonstrate:

- ✓ accepts WorkflowRequest
- ✓ acknowledges ownership
- ✓ reports RUNNING
- ✓ reports blockers
- ✓ creates HumanTask when appropriate
- ✓ resumes after resolution
- ✓ verifies outcome
- ✓ reports COMPLETED
- ✓ correlates everything with case_id

This is the gate for joining the final simulation (§19). System-behavior evaluation additionally guards the engine against pathological dynamics: no infinite delegation; no excessive retries; no case contamination; no premature completion (§24).

## 8. Approval / human-in-the-loop system

Human intervention is **first-class state, not chat improvisation** (§15).

**Task types** (§15): `APPROVAL`, `MISSING_INFORMATION`, `CONFLICT_RESOLUTION`, `EXCEPTION_HANDLING`, `MANUAL_ACTION`.

**Persistence** — every HumanTask persists (§15):

```
human_task_id
case_id
workflow_id
requested_by
requested_from
type
context
allowed_actions
status
decision
resolved_by
timestamps
```

`case_id` and `workflow_id` bind the task to the case and workflow; `requested_by`/`requested_from` record who asked whom; `allowed_actions` bounds what the human may do; `decision` and `resolved_by` make the outcome auditable.

**The loop** (§15):

```
WAITING_FOR_HUMAN
        ↓
human decision
        ↓
event
        ↓
coordinator resumes workflow
```

A workflow in `WAITING_FOR_HUMAN` is not parked in a chat thread — the Human Task Service holds it as state, emits the decision as a structured event (`APPROVAL_GRANTED` in the trace, §23), and the coordinator resumes the workflow (§11, §15).

**Human behavior as injection** (§17.4): approval; rejection; no response; ambiguous response; unauthorized approval — testing HITL and escalation. The final chaos scenario exercises this: "One approval isn't answered. Another approval comes from unauthorized person." (§20). The Onboarding Agent's responsibilities include requesting human intervention and escalating stalled work (§11).

Open questions: the spec does not define who may resolve a task (authorization), what happens on "no response" (SLA/timeout), or how an "unauthorized approval" is detected — see §13.

## 9. Knowledge and memory layer

**Abstraction.** Agents depend on `KnowledgeProvider`, never on a concrete source (§4):

```
KnowledgeProvider
├── MarkdownKnowledgeProvider      ← hackathon
└── ConfluenceKnowledgeProvider    ← later
```

with the same conceptual interface: `search(query)`, `get_document(id)` (§6).

**Hackathon implementation.** `MarkdownKnowledgeProvider("./knowledge")` — each team's `knowledge/{team}/` directory of Markdown files (§5, §6). **For the MVP, don't overbuild RAG.** The corpus is tiny. Loading relevant Markdown into agent context or implementing basic file search is sufficient (§6).

**Frontmatter.** Simple frontmatter where useful: `title`, `owner`, `status`, `updated` (§6) — the metadata that makes retrieval, staleness, and conflict observable.

**Usage in the agent template** (§5):

```python
agent = TeamAgent(
    id="device-agent",
    goal="employee_device_ready",
    instructions="instructions.md",
    knowledge=MarkdownKnowledgeProvider("./knowledge"),
    tools=[check_inventory, get_assignment, reserve_device,
           get_delivery_status, request_replacement],
)
```

The platform's wrapper handles the Agent Lab plumbing (§5).

**Injection surface.** Knowledge-condition injection (outdated document, conflicting policy, missing policy, ambiguous exception — §17.3) tests the reasoning over this layer; for the MVP it is scenario-specific Markdown directories (§17.3). Knowledge reads are observable: `KNOWLEDGE_READ substitution-policy.md` appears on the trace (§23).

**Memory.** The spec defines agent memory as knowledge + observed state: agents "reason using knowledge + observed state" (§1) where state is what their tools return from MockWorld. No additional agent memory store (conversation memory, vector store) is specified — see §13 open questions.

## 10. Observability and audit

**Event Store.** Record **observable actions rather than private model reasoning** (§23). The trace timeline (§23):

```
10:31:00 CASE_CREATED
10:31:02 Onboarding → Device   WORKFLOW_DELEGATED
10:31:04 Device                TOOL_CALL check_inventory
10:31:04 MockWorld             TOOL_RESULT available=0
10:31:07 Device                KNOWLEDGE_READ substitution-policy.md
10:31:10 Device                BLOCKER_CREATED
10:31:11 Device                HUMAN_TASK_CREATED
10:32:15 Human                 APPROVAL_GRANTED
10:32:17 Device                TOOL_CALL reserve_device
10:32:20 Device                OUTCOME_VERIFIED
```

This is the foundation for debugging and evaluation (§23). The Trace tab renders it in the UI (§21); the Evaluation Engine consumes it (expected/forbidden events, §16; trajectory assertions like `approval_requested`, `outcome_verified`, `failure_retried`, `ownership_escalated`, §24).

**Structured events vs natural-language channels.** Channels are the visible, human-readable layer; the event stream is the machine-readable substrate: "Natural-language communication is visible. Structured events travel underneath." (§13). Audit value lives in the structured layer — every event, task, and blocker is correlated with `case_id` (§19).

**Human-only audit view.** The world inspector lets humans inspect reality (E42 employee/device/inventory/access/applications) and compare "what reality contains vs what their agent believes" (§22). Agents do not have this privileged view (§22).

**Scenario results.** Teams see per-scenario PASS/FAIL with Expected vs Observed events, final state, and a trace link — a tight development loop (§25).

Open questions: event schema details (fields beyond the trace examples), retention, and channel message retention are not specified — see §13.

## 11. API and data model

### MockWorld API (§8)

**Agent-facing APIs** — agents access these ONLY through ADK tools:

```
GET  /world/employees/{id}
GET  /world/devices/inventory
GET  /world/devices/{employee}
POST /world/devices/{employee}/reserve
POST /world/devices/{employee}/replace
GET  /world/access/{employee}
POST /world/access/{employee}/request
GET  /world/access/{employee}/requests
GET  /world/systems/{employee}
GET  /world/applications/{employee}
POST /world/applications/{employee}/provision
```

**Simulation APIs** — agents cannot access:

```
POST /simulation/reset
POST /simulation/load
POST /simulation/mutate
POST /simulation/faults
POST /simulation/events
```

Example mutation:

```json
POST /simulation/mutate
{
  "path": "inventory.macbook_pro_14.available",
  "value": 0
}
```

"The scenario engine effectively plays reality." (§8)

### SQLite schema (§9)

Suggested initial tables:

```
employees
managers
inventory
devices
device_orders
identities
groups
entitlements
access_requests
systems
system_accounts
applications
application_access
onboarding_cases
workflow_runs
human_tasks
events
```

**Do not expose SQLite directly to agents.** (§9)

### Domain tools (§10)

Ordinary ADK function tools calling MockWorld HTTP endpoints. **No MCP required.**

| Agent | Tools | Owned outcome |
|---|---|---|
| Device | `get_employee_device_requirements`, `check_inventory`, `get_device_assignment`, `reserve_device`, `get_delivery_status`, `request_replacement` | `employee_device_ready` |
| Access | `get_identity`, `get_current_entitlements`, `get_access_requirements`, `request_entitlement`, `verify_entitlement` | `employee_access_ready` |
| Systems | `get_required_systems`, `get_account_status`, `provision_account`, `verify_account` | `employee_systems_ready` |
| Applications | `get_required_applications`, `get_application_access`, `provision_application`, `verify_application_access` | `employee_applications_ready` |

### Protocol and scenario data models

- Workflow contract: `WorkflowRequest` / `Status` / `Outcome` (§12) — see §7.
- HumanTask: the §15 persistence fields — see §8.
- Scenario: YAML with `id`, `initial_state`, `events`, `expected` (`required_events`, `allowed_final_states`, `forbidden_events`) (§16).
- Event types: the observable-action vocabulary of §23 (e.g., `CASE_CREATED`, `TOOL_CALL`, `TOOL_RESULT`, `KNOWLEDGE_READ`, `BLOCKER_CREATED`, `HUMAN_TASK_CREATED`, `APPROVAL_GRANTED`, `OUTCOME_VERIFIED`).

## 12. Deployment topology

Per §3 and §27:

```
┌──────────────────────────── SHARED MACHINE ────────────────────────────┐
│  Agent Lab UI (React/Vite)                                             │
│  Agent Lab Backend (single FastAPI process):                           │
│    Agent Router · Channel Service · Case Store · Event Store           │
│    Scenario Engine · Evaluation Engine · Human Task Service            │
│    MockWorld API ──► SQLite (mock-world/schema.sql + seeds)            │
│  Onboarding Agent (Google ADK)                                         │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ Agent protocol (WebSocket + HTTP)
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
  Participant laptop 1   Participant laptop 2   ... (team agents, Google ADK)
  Device Agent           Access Agent               Markdown KB + ADK Tools
```

- Everything can initially run on **one shared machine except team agents, which can run on participants' laptops** (§3).
- One backend process contains FastAPI ├── Agent Router, Channels, Case Store, Human Tasks, Scenario Engine, Evaluation Engine, Event Store, MockWorld (§27).
- Agent communication is **WebSocket + HTTP** (§27); team agents register locally and join the shared environment (§26, §29).
- **Do not introduce distributed infrastructure yet** (§27). Deferred: Slack, Confluence, MCP, SSO, production IAM, Kubernetes, Kafka, advanced RAG, visual workflow builder, LLM-generated scenarios, production deployment (§29) — abstractions anticipated, not implemented.

### Production migration path (§30)

| Hackathon | Production |
|---|---|
| Markdown | Confluence |
| MockWorld | Enterprise APIs |
| ADK function tools | direct API tools / MCP where appropriate |
| Agent Lab channels | Slack |
| SQLite | durable workflow/event store |
| Local ADK | managed agent runtime |
| Scenario Engine | pre-production assurance harness |

The scenario/evaluation machinery can evolve into the certification environment for Enterprise Operations agents. The strategic asset is the feedback loop: Build → Domain scenarios → Contract certification → Multi-agent simulation → Safety/policy evaluation → Human review → Deploy → Production traces → Failure → new regression scenario → back to evaluation (§30).

## 13. Risks, trade-offs, open questions

### Risks (with spec-grounded mitigations)

| Risk | Mitigation in spec | Residual |
|---|---|---|
| Hidden scenario leakage invalidates final simulation | Hidden scenarios not distributed to participants (§28); Platform Team owns final scenarios (§20) | Distribution hygiene (git history, shared folders) not addressed; see THREAT_MODEL.md T-03 |
| Agents cheat by observing privileged state | Agents access world APIs only through ADK tools (§8); no direct SQLite exposure (§9); no world-inspector view for agents (§22) | No authentication mechanism specified (SSO/IAM deferred, §29) |
| Faults and lies erode trust in outcomes | Deterministic state assertions check actual world state (§24); contract requires outcome verification (§19) | An agent that verifies through the same lying tool may pass narrative checks; state assertions are the backstop |
| LLM nondeterminism undermines certification | Deterministic evaluation preferred; LLM-as-judge avoided as pass/fail (§24) | Trajectory assertions are event-based; see THREAT_MODEL.md T-07 |
| Case contamination across concurrent cases | Correlation with `case_id` (§19); system-behavior eval: no case contamination (§24) | Concurrency model for multiple simultaneous cases not specified (finals run 12 starters, §20) |
| Laptop agents flaky on shared network | WebSocket + HTTP; local agent registration (§26, §27) | Reconnect/timeout semantics not specified |

### Trade-offs (as the spec frames them)

- **One process vs distributed.** Everything in one FastAPI process; "Do not introduce distributed infrastructure yet" (§27) — favors hackathon velocity, defers scalability.
- **Markdown vs Confluence/RAG.** "For the MVP, don't overbuild RAG" (§6) — favors simplicity; the abstraction keeps the later swap cheap.
- **Deterministic eval vs LLM-as-judge.** Deterministic evaluation is primary; "Avoid making LLM-as-judge the main pass/fail mechanism" (§24) — favors objectivity and tight loops over narrative grading.
- **Scenario controls the world, not the agents** (§16) — agents are never told what happened; they must perceive it, which is the point of the lab, but makes scenarios harder to author.
- **Natural-language visible, structured events underneath** (§13) — channels are the human window; the event store is the audit/eval substrate.
- **One shared machine + participant laptops** (§3) — zero infra friction, but the platform trusts participant code on the network (see THREAT_MODEL.md).

### Open questions (spec is silent — TBD, do not fabricate)

1. Authentication/authorization: the spec has no credentials for the hackathon; SSO and production IAM are deferred (§29). How are `/simulation/*` and `/world/*` protected from direct HTTP calls by participants? (THREAT_MODEL.md T-02, T-08)
2. Approval authority: how is an approval determined to be authorized or "from unauthorized person" (§17.4, §20)? Who may resolve a HumanTask, and how is identity captured beyond `requested_by`/`resolved_by` (§15)? (T-04)
3. HumanTask lifecycle: SLA/timeout for "no response" (§17.4); semantics of `allowed_actions`; whether a task can be re-opened. (T-04)
4. Workflow state machine details: exact transition rules for `BLOCKED` → resumed, retry semantics from `FAILED`, and limits on delegation depth/retries ("no infinite delegation; no excessive retries" are evaluated, §24, but no numeric bounds are given). (T-09)
5. Event schema: fields beyond the §23 trace examples; retention; channel message retention (§10).
6. Concurrency: how multiple simultaneous cases are isolated within one agent process (finals run 12 starters, §20). (T-05)
7. Memory: no agent memory store beyond knowledge + observed state is specified (§9).
8. MockWorld enforcement of tool identity: the spec separates tool sets per domain (§10) but is silent on server-side checks that a given agent only calls its own domain's endpoints. (T-10)
9. Fault semantics: which tools may lie (`success_without_state_change`, §17.2), and whether verification reads (`verify_*` tools) are ever subject to faults. (T-06)
