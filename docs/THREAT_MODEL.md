# Agent Lab — Threat Model

> Status: Draft · Derived from [SPEC.md](../SPEC.md) (authoritative ground truth; section references like §8 point there)
> Companion document: [ARCHITECTURE.md](ARCHITECTURE.md) (trust boundaries, §5)

## 0. Purpose, scope, and method

Agent Lab is a development, simulation, and evaluation environment for team-owned operational AI agents (SPEC §1). This threat model covers the hackathon/MVP system as specified. It is a **lab-integrity** threat model first: the assets that matter are scenario confidentiality, world-state integrity, evaluation validity, case/employee isolation, human-in-the-loop integrity, domain encapsulation, and platform availability. MockWorld data is simulated — there is no real employee PII — but the lab is a dress rehearsal for production ("the architecture you could eventually productionize"), so boundary discipline here is the rehearsal for production boundaries (§30).

**Threat actors:**

| Actor | Capabilities per spec |
|---|---|
| Participants (teams) | Own their agent code, `knowledge/*.md`, `tools/*`, `scenarios/` on their laptops; register agents locally; chat in channels; see their own certification results (§5, §25, §26) |
| Agents (LLM behavior) | Execute only their own ADK tools (§10); read their team's knowledge; can be manipulated via injected content (§17) |
| Humans acting on tasks | Approve/reject/resolve HumanTasks; four failure modes tested: approval, rejection, no response, ambiguous response, unauthorized approval (§17.4, §20) |
| Scenario authors (Platform Team) | Author team packs (§18) and hidden scenarios (§20, §28); trusted |
| External network actors | Not in scope of the spec (lab network, one shared machine + laptops, §3); no authentication mechanism specified |

**Method.** Each threat is analyzed with seven fixed fields: attack path, asset at risk, security boundary, preventive controls, detective controls, recovery controls, residual risk. Every control is taken verbatim or directly from the spec — nothing is invented. Where the spec is silent, the field says **Open question** or **TBD**. In particular, the spec defines **no credentials**: SSO and production IAM are deferred (§29), so where a control would require authentication, that is flagged rather than assumed.

**Security-boundary reference** (from ARCHITECTURE.md §5, all spec-derived):

| Boundary | Spec basis |
|---|---|
| Agent-facing `/world/*` vs privileged `/simulation/*` | "Separate normal business APIs from privileged simulation APIs" (§8); "Simulation APIs — agents cannot access" (§8) |
| Agents vs MockWorld SQLite | "Do not expose SQLite directly to agents" (§9) |
| Teams vs platform | Platform Team owns coordinator, simulation engine, shared UI, contracts, evaluation harness (§1); hidden scenarios not distributed (§28) |
| Humans vs agents | HumanTask `requested_by`/`requested_from`/`resolved_by` (§15); world inspector is human-only, "Agents do not have this privileged view" (§22) |
| Domain isolation | "Each domain agent gets only its own tools" (§10); per-team knowledge dirs (§6); per-domain channels (§13) |

---

## T-01 — Prompt injection via Markdown knowledge documents

A knowledge document (or `instructions.md`) contains instructions hostile to the agent's task — deliberately (a team sabotaging another's corpus is out of scope; the realistic path is accidental or scenario-authored) or via a scenario's knowledge-condition injection (outdated document, conflicting policy, missing policy, ambiguous exception — §17.3). The loaded Markdown is rendered into the agent's context; the model follows the injected text instead of its actual policy, e.g. reserving an unavailable device or granting privileged access.

| Field | Analysis |
|---|---|
| **Attack path** | Scenario engine loads a poisoned `knowledge/*.md` (knowledge-condition injection, §17.3) → agent's `KnowledgeProvider` returns it via `search(query)`/`get_document(id)` (§6) → document text enters model context → model acts on injected directives → policy violation (e.g., privileged access granted without approval) |
| **Asset at risk** | Agent behavior integrity; safety invariants ("no privileged access without approval; no unavailable device reserved; no duplicate provisioning", §24); certification validity |
| **Security boundary** | Knowledge layer (ARCHITECTURE.md boundary 5). Knowledge is a data input to the agent, not a trusted instruction channel |
| **Preventive controls** | Spec-derived: agents "reason using knowledge + observed state" (§1) — knowledge is advisory, tools are the only world access; MVP keeps the corpus tiny and team-owned (§6); knowledge-condition injections are exactly what scenarios test (§6, §17.3). Not specified: sanitization/instruction hardening of knowledge content, or separation of `instructions.md` from data documents — **Open question** |
| **Detective controls** | Trace records `KNOWLEDGE_READ <doc>` events (§23); scenario `expected` blocks assert `required_events`/`forbidden_events` (§16); safety invariants and state assertions catch the downstream action regardless of cause (§24) |
| **Recovery controls** | The dev loop — edit `knowledge/*.md`, restart, re-run scenario, inspect trace (§26); scenario reset via `/simulation/reset` (§8) |
| **Residual risk** | **High.** The spec provides no defense-in-depth against injected instructions; detection is post-hoc via evaluation. In production (Confluence knowledge), this becomes a first-class supply-chain concern — **Open question** for the lab |

---

## T-02 — Unauthorized access to privileged `/simulation/*` endpoints

A participant's agent code — or a participant directly — calls `POST /simulation/reset|load|mutate|faults|events` (§8) to reset the world, re-roll faults, or read scenario events, corrupting the exercise.

| Field | Analysis |
|---|---|
| **Attack path** | Agent tool defined by a team calls `POST /simulation/mutate` directly (bypassing the intended tool surface); or a participant curls the shared machine's `/simulation/*` endpoints directly; or an agent inherits a tool that proxies unvalidated paths |
| **Asset at risk** | World-state integrity; scenario validity; evaluation validity; the "scenario engine effectively plays reality" invariant (§8) |
| **Security boundary** | Agent-facing vs simulation APIs (ARCHITECTURE.md boundary 1): "Separate normal business APIs from privileged simulation APIs" (§8) |
| **Preventive controls** | Spec-derived: "Simulation APIs — agents cannot access" (§8); agents access world APIs "ONLY through ADK tools" (§8); "Each domain agent gets only its own tools" (§10); separation is enforced by design (separate route groups + no tool wrappers for `/simulation/*`) and asserted in tests (repo non-negotiable). Not specified: any authentication/authorization on the endpoints, or network isolation of the shared machine — SSO/IAM deferred (§29) — **Open question** |
| **Detective controls** | Event Store records `TOOL_CALL`/`TOOL_RESULT` and world changes appear on the trace (§23); unexpected state changes are visible to humans via the world inspector ("what reality contains vs what their agent believes", §22); evaluation's state assertions and forbidden events (§16, §24) fail the scenario if the world was tampered |
| **Recovery controls** | `/simulation/reset` + `/simulation/load` restore the canonical scenario state (§8); re-run the scenario |
| **Residual risk** | **High** for direct network calls: with no credential layer, any participant on the lab network can reach the shared backend. The design separation stops accidental agent-tool access, not deliberate HTTP calls. Mitigation options (network isolation, tokens, per-agent API identity) are **Open question / TBD** — not in the spec |

---

## T-03 — Hidden scenario leakage (participants must never see `scenarios/hidden/`)

The final multi-agent scenarios are Platform-Team-owned and unknown to teams: "Teams should not know the exact failures" (§20). If `scenarios/hidden/` YAML reaches participants — via the distributed repo, git history, shared folders, UI exposure, or verbose errors — the unknown scenario and chaos scenario become rehearsable and the simulation is invalidated.

| Field | Analysis |
|---|---|
| **Attack path** | `scenarios/hidden/` files shipped in a participant distribution; leaked via git history; exposed by a UI/scenario endpoint that lists or renders hidden scenario definitions; error messages echoing scenario YAML |
| **Asset at risk** | Confidentiality of hidden scenarios (§20, §28); validity of the final simulation and of the "Are Monday's new joiners ready?" verdict (§20) |
| **Security boundary** | Teams vs platform (ARCHITECTURE.md boundary 3): Platform Team owns the final scenarios; "The hidden scenarios should obviously not be distributed to participants" (§28) |
| **Preventive controls** | Spec-derived: separate distributions — team certification packs (§18) are known and iterative ("Teams know these scenarios and iterate until they pass", §18), hidden scenarios are platform-owned (§20); repo layout keeps `scenarios/hidden/` distinct (§28). Not specified: the exact distribution mechanism, gitignore/archive policy, or UI access control — **Open question** (repo convention: gitignored or private archive) |
| **Detective controls** | Not specified in the spec. Candidates: distribution manifest review; anomalous performance on the unknown scenario (a team passing without visible discovery); trace inspection. **TBD** — no spec-grounded control exists |
| **Recovery controls** | Not specified. Scenario rotation/regeneration would be needed; **TBD** |
| **Residual risk** | **Medium.** Purely a matter of distribution hygiene; the spec's only control is the rule "should obviously not be distributed to participants" (§28). Git history is a classic leak vector; see open questions |

---

## T-04 — Unauthorized or impersonated human approvals

A HumanTask (`APPROVAL`, etc., §15) is resolved by someone without authority — the chaos scenario explicitly includes "another approval comes from unauthorized person" (§20) — or an agent impersonates a human in a channel to manufacture consent. An improper `APPROVAL_GRANTED` unblocks a policy-gated action (e.g., device substitution, privileged access).

| Field | Analysis |
|---|---|
| **Attack path** | Human (or agent) resolves a task they are not authorized for; an agent posts to `#onboarding`/private chats claiming human authority; an ambiguous response is interpreted as approval; a "no response" is auto-approved by some fallback |
| **Asset at risk** | HITL integrity; safety invariant "no privileged access without approval" (§24); audit truth of `decision`/`resolved_by` (§15) |
| **Security boundary** | Humans vs agents (ARCHITECTURE.md boundary 4); the decision → event → resume loop (§15) |
| **Preventive controls** | Spec-derived: HumanTask persists `requested_by`, `requested_from`, `type`, `context`, `allowed_actions`, `status`, `decision`, `resolved_by`, `timestamps` (§15) — an audit record exists; human-behavior injections (approval, rejection, no response, ambiguous response, unauthorized approval) test HITL and escalation (§17.4); Onboarding Agent "request[s] human intervention" and "escalate[s] stalled work" (§11). Not specified: **any mechanism to verify an approver's authority or identity** — this is a core gap; SSO/IAM deferred (§29) |
| **Detective controls** | Trace shows `HUMAN_TASK_CREATED` → `APPROVAL_GRANTED` with timestamps (§23); `human_tasks` table records `resolved_by` (§15, §9); trajectory assertion `approval_requested` and safety invariants (§24) fail if approval was not legitimately requested/granted |
| **Recovery controls** | Reject/reverse the decision and re-verify the entitlement/device state (state assertions check the world, §24); scenario re-run; `/simulation/reset` (§8) |
| **Residual risk** | **Medium–High.** The audit trail exists, but the spec defines no authorization check, so "unauthorized approval" can only be detected by evaluation outcomes, not prevented. How the system is supposed to recognize the unauthorized approver in the chaos scenario is **Open question** |

---

## T-05 — Case contamination (agent A acting on case B; cross-employee data bleed)

A domain agent working ONB-42/E42 acts on ONB-43/E43 — wrong `employee_id` in a tool call, state from one case leaking into another's context, or a single agent instance interleaving two workflows (the finals run 12 starters, §20; the integration scenario runs 5 employees, §20).

| Field | Analysis |
|---|---|
| **Attack path** | Agent reads/writes `GET /world/devices/{employee}` or `POST .../reserve` for the wrong employee; a shared agent process mixes conversation/tool state between cases; a coordinator delegates the same outcome twice to different agents |
| **Asset at risk** | Per-case correctness; per-employee isolation; state assertions ("device actually assigned", "correct entitlement actually granted", §24); the readiness verdict (§20) |
| **Security boundary** | Case/workflow correlation; domain isolation (ARCHITECTURE.md boundary 5) |
| **Preventive controls** | Spec-derived: `WorkflowRequest` carries `{workflow_id, case_id, goal, employee_id, context}` (§12) — every delegated unit of work is bound to one case and one employee; contract certification requires the agent to "correlate everything with case_id" (§19); system-behavior evaluation checks "no case contamination" (§24); domain tools take the employee as an argument (§8, §10). Not specified: the concurrency model — whether one agent process handles multiple cases and how context is isolated — **Open question** |
| **Detective controls** | System-behavior evaluation: "no case contamination" (§24); trace timeline correlated per case (`CASE_CREATED`, `WORKFLOW_DELEGATED`, §23); state assertions verify the world state for the *right* employee (§24); scenario `expected`/`forbidden` events (§16) |
| **Recovery controls** | Scenario re-run; `/simulation/reset` + `/simulation/load` restore canonical state (§8) |
| **Residual risk** | **Medium.** Correlation is contractual and evaluated, but nothing structurally prevents an agent from passing a wrong `employee_id` (tool args are agent-chosen), and multi-case concurrency is unspecified |

---

## T-06 — Tool fault injection misuse / `success_without_state_change` lying about effects

Fault injection includes "timeout; 500; stale response; success_without_state_change" (§17.2) — the chaos scenario has "E08 → application provisioning lies about success" (§20). The danger is not the fault itself but the agent reporting success on a lie: it believes it reserved/provisioned/granted when the world did not change, then emits `OUTCOME_VERIFIED`/`COMPLETED` on that belief — a false readiness verdict. Misuse in the other direction: an agent exploits a fault as an excuse to stop working, or over-retries.

| Field | Analysis |
|---|---|
| **Attack path** | Scenario engine injects fault via `/simulation/faults` (§8, §17.2) → tool returns `success_without_state_change` (or stale response) → agent treats the response as truth → `OUTCOME_VERIFIED` on unverified state → coordinator marks the case ready |
| **Asset at risk** | Truthfulness of `verified` outcomes (§12); the readiness verdict (§20); evaluation validity |
| **Security boundary** | Domain tools → MockWorld boundary; perception vs reality (the scenario engine "plays reality", §8) |
| **Preventive controls** | Spec-derived: contract certification requires the agent to "verify outcome" and "report COMPLETED" only after verification (§19); tools return state so perception is checkable; the scenario model's `expected` block forbids e.g. `unavailable_device_reserved` (§16); evaluation is state-based, not narrative-based ("Final world-state correctness 35%", §24). Not specified: which tools can lie, whether `verify_*` reads can themselves be faulted, and how a client distinguishes `success_without_state_change` from genuine success — **Open question** |
| **Detective controls** | State assertions check the actual world ("device actually assigned; correct entitlement actually granted; application actually provisioned", §24); `TOOL_RESULT` events on the trace vs final world state (§23); forbidden events (§16); trajectory assertion `failure_retried` (§24) |
| **Recovery controls** | Agent retry after detecting the lie (`failure_retried` trajectory, §24); scenario re-run; `/simulation/reset` (§8) |
| **Residual risk** | **Medium.** Deterministic state assertions are the backstop, but if the agent's verification path uses the same lying tool and the final state is *checked* by the same faulted read, the lie can survive. Fault semantics are underspecified — **Open question** |

---

## T-07 — LLM-as-judge gaming (evaluation manipulation)

If evaluation leaned on an LLM judge, agents could be optimized to produce judge-pleasing narratives ("I followed policy") while doing nothing, or to emit the right-sounding events without the underlying state. The spec explicitly forecloses this: "Avoid making LLM-as-judge the main pass/fail mechanism" (§24).

| Field | Analysis |
|---|---|
| **Attack path** | Agent emits narrative/events that satisfy trajectory assertions (`approval_requested`, `outcome_verified`, §24) without the corresponding world change; or a team exploits knowledge of the per-scenario Expected vs Observed output (§25) to tailor event sequences |
| **Asset at risk** | Evaluation validity; certification integrity |
| **Security boundary** | Evaluation harness (Platform Team-owned, §1); scenario controls the world, not the agents (§16) |
| **Preventive controls** | Spec-derived: deterministic evaluation is preferred — state assertions (world-state checks), safety invariants, trajectory assertions, system behavior (§24); weights favor verifiable outcomes (Final world-state correctness 35%, Policy/safety 25%, §24); scenario `expected` blocks are event assertions on real observables (§16); LLM-as-judge is explicitly not the pass/fail mechanism (§24) |
| **Detective controls** | State assertions compare the world, not the story (§24); world inspector lets humans compare "what reality contains vs what their agent believes" (§22); `forbidden_events` catch gamed actions (§16) |
| **Recovery controls** | Scenario re-run; evaluation revision (Platform Team owns the harness, §1) |
| **Residual risk** | **Low.** The design removes the main gaming surface. Residual: trajectory assertions are event-based and could in principle be emitted without actions — state assertions and safety invariants are the backstop, and event definitions are platform-owned |

---

## T-08 — MockWorld state tampering

The SQLite database behind MockWorld is modified outside the sanctioned paths — direct file access (the DB lives on the shared machine; `mock-world/schema.sql` and `seeds` are in the repo, §28), crafted `/world/*` payloads, or illegitimate `/simulation/mutate` (see T-02). Result: the world no longer matches the scenario, evaluations are meaningless, and agents learn from a corrupted reality.

| Field | Analysis |
|---|---|
| **Attack path** | Participant edits the SQLite file directly; calls `/world/*` with crafted payloads (e.g., reserving devices under a fake employee); replays `/simulation/mutate` (T-02); truncates the `events` table to erase an audit trail |
| **Asset at risk** | World-state integrity; evaluation determinism; audit trail (`events` table, §9) |
| **Security boundary** | Agents vs MockWorld SQLite: "Do not expose SQLite directly to agents" (§9); agent-facing vs simulation APIs (§8) |
| **Preventive controls** | Spec-derived: no direct SQLite exposure — agents reach MockWorld only through their ADK tools (§9, §10); world mutation happens only through sanctioned business semantics (`reserve`, `replace`, `request`, `provision`, §8) or the privileged `/simulation/*` APIs (§8). Not specified: DB file permissions, endpoint authentication, or payload validation details — **Open question** |
| **Detective controls** | World inspector (human-only) exposes reality for comparison with agent belief (§22); state assertions (§24); trace events (`TOOL_RESULT`) vs world state (§23); `forbidden_events` (§16) |
| **Recovery controls** | `/simulation/reset` + `/simulation/load` restore the seeded scenario state (§8); re-run scenario and evaluation |
| **Residual risk** | **Medium–High.** The design keeps agents off SQLite and confines mutation to the API, but the file itself and the unauthenticated endpoints are reachable by participants on the shared machine/network (no credentials — SSO/IAM deferred, §29) |

---

## T-09 — Infinite delegation or excessive retries (resource exhaustion / DoS)

A coordinator that keeps re-delegating an outcome, or a domain agent that retries a failing tool forever (the chaos scenario has "E11 → Systems Agent experiences tool timeout", §20), or a workflow stuck in `WAITING_FOR_HUMAN` because "one approval isn't answered" (§20) — unbounded loops consume LLM tokens, CPU, event-store growth, and human attention, and can stall the entire simulation.

| Field | Analysis |
|---|---|
| **Attack path** | Onboarding Agent delegates → domain agent reports `BLOCKED`/`FAILED` → coordinator re-delegates the same outcome; agent loops `TOOL_CALL` on a faulted tool; a task waits indefinitely on an unanswered approval (§17.4, §20) |
| **Asset at risk** | Platform availability; scenario completion within the exercise; event-store growth (`events` table, §9); cost |
| **Security boundary** | Workflow engine / coordinator (ARCHITECTURE.md §7); HITL loop (§15) |
| **Preventive controls** | Spec-derived: the state machine has terminal states `FAILED`/`COMPLETED` (§12); the coordinator owns process control — track progress, reconcile blockers, coordinate dependencies, escalate stalled work (§11); system-behavior evaluation asserts "no infinite delegation; no excessive retries; no premature completion" (§24); contract certification requires reporting blockers and resuming after resolution (§19) — a well-behaved agent stops and asks. Not specified: **numeric bounds** (max retries, max delegation depth, timeouts, task SLA) — **Open question / TBD** |
| **Detective controls** | Trace timeline exposes repeated `TOOL_CALL`/`BLOCKER_CREATED`/`WORKFLOW_DELEGATED` patterns (§23); system-behavior evaluation flags the violations (§24); humans see the case rollup (Blockers: 1, Approvals: 1, §21) |
| **Recovery controls** | Human intervention/escalation path (§11, §15); scenario reset and re-run (§8) |
| **Residual risk** | **Medium.** Detection exists via evaluation and traces, but with no configured limits the platform depends on agents behaving and evaluators noticing; a single misbehaving agent can consume the shared machine's resources until a human intervenes |

---

## T-10 — Cross-domain data bleed between team agents (e.g., Access agent reading device data)

A domain agent observes or acts on another domain's world: the Access Agent calls `GET /world/devices/inventory` or reads `knowledge/devices/*.md`; a Systems Agent reads applications data; agents leak observations into shared channels (`#onboarding` is shared, §13). This breaks domain encapsulation ("Each domain agent gets only its own tools", §10) and can distort decisions (e.g., access decisions biased by inventory knowledge) and contaminate per-domain evaluation.

| Field | Analysis |
|---|---|
| **Attack path** | Team implements a tool in `tools/access.py` that hits a device endpoint (§8); agent's context includes another domain's knowledge files; a domain agent posts another domain's state to a shared channel; a coordinator leaks case context across delegations |
| **Asset at risk** | Domain encapsulation (§11: "Domain implementation remains encapsulated"); per-domain evaluation independence; channel trust |
| **Security boundary** | Domain isolation (ARCHITECTURE.md boundary 5): per-domain tools (§10), per-domain knowledge dirs (§6), per-domain channels + private conversations (§13) |
| **Preventive controls** | Spec-derived: "Each domain agent gets only its own tools" (§10) — the template ships only the domain's tool set (§5); knowledge is per-team directories (`knowledge/{access, devices, systems, applications, onboarding}`, §6); channels are per-domain plus private agent conversations (§13); the Onboarding Agent delegates outcomes, not domain actions, so it never reaches into domain implementation (§11); agents lack the world inspector's privileged view (§22). Not specified: **server-side enforcement** that an agent only calls its own domain's endpoints (e.g., per-agent identity on MockWorld), or channel read restrictions — **Open question** |
| **Detective controls** | Trace shows every `TOOL_CALL` with its actor (§23) — cross-domain tool calls are visible; scenario `forbidden_events` (§16) and system-behavior checks (§24) can flag them; evaluation's per-domain state assertions fail if the wrong domain acted |
| **Recovery controls** | Restrict the tool registration for the offending agent; re-run the scenario; `/simulation/reset` (§8) |
| **Residual risk** | **Medium.** Enforcement is by template design and evaluation, not by the platform: participants control their agent's code on their laptops, so nothing technical stops a team from adding a cross-domain tool. Server-side enforcement would be needed for a stronger guarantee — **Open question** |

---

## Summary matrix

| # | Threat | Primary boundary | Key spec controls | Residual risk |
|---|---|---|---|---|
| T-01 | Prompt injection via knowledge | Knowledge layer | Eval backstops (state/safety invariants §24); trace `KNOWLEDGE_READ` §23 | High |
| T-02 | Unauthorized `/simulation/*` access | World vs simulation APIs | Design separation §8; no tool wrappers; no credentials specified | High |
| T-03 | Hidden scenario leakage | Teams vs platform | "should obviously not be distributed to participants" §28 | Medium |
| T-04 | Unauthorized/impersonated approvals | Humans vs agents | HumanTask audit fields §15; safety invariants §24 | Medium–High |
| T-05 | Case contamination | Case/workflow correlation | `case_id` correlation §19; "no case contamination" §24 | Medium |
| T-06 | Fault lies / `success_without_state_change` | Tools → MockWorld | State assertions §24; verify outcome §19 | Medium |
| T-07 | LLM-as-judge gaming | Evaluation harness | Deterministic eval; "Avoid making LLM-as-judge…" §24 | Low |
| T-08 | MockWorld state tampering | Agents vs SQLite | "Do not expose SQLite directly to agents" §9; `/simulation/reset` §8 | Medium–High |
| T-09 | Infinite delegation / retries | Workflow engine | System-behavior eval §24; escalation §11 | Medium |
| T-10 | Cross-domain data bleed | Domain isolation | "only its own tools" §10; per-domain knowledge/channels §6, §13 | Medium |

## Cross-cutting open questions (spec is silent — TBD, do not fabricate)

1. **No credential layer anywhere.** SSO and production IAM are deferred (§29). Endpoint authentication for `/world/*` and `/simulation/*`, DB file protection, and any network isolation of the shared machine are unspecified (affects T-02, T-08).
2. **Approver authorization.** How an approval is recognized as "from unauthorized person" (§17.4, §20) is unspecified; `resolved_by` records identity but nothing verifies it (T-04).
3. **Numeric limits.** Max retries, delegation depth, timeouts, and HumanTask SLA ("no response", §17.4) are unset; evaluation only asserts "no excessive retries" qualitatively (§24) (T-09).
4. **Server-side domain enforcement.** MockWorld has no per-agent identity, so cross-domain tool calls are preventable only by template design and detectable only via traces (T-10).
5. **Fault semantics.** Which tools may lie, and whether `verify_*` reads can be faulted (T-06).
6. **Hidden-scenario distribution mechanics.** Gitignore/archive policy, UI access control, and leakage detection are unspecified (T-03).
7. **Concurrency.** How multiple simultaneous cases are isolated within one agent process (T-05; finals run 12 starters, §20).
