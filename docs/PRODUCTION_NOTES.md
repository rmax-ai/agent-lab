# Production notes

What the lab is today operationally, what it deliberately is not, and what
breaks first on the path from the single-process hackathon topology to a
multi-process deployment (SPEC §30). Everything here describes the code as
shipped: `backend/src/agentlab/backend/app.py`,
`mock-world/src/agentlab/world/app.py`, `cli/src/agentlab/cli/servers.py`.

## Deployment topology

Three processes in development, all on loopback:

| Component | Port | Process |
|---|---|---|
| Backend (Agent Lab platform API) | 8080 | FastAPI via uvicorn |
| MockWorld (simulated external reality) | 8000 | FastAPI via uvicorn |
| Frontend console | 5173 | vite dev server |

`agent-lab dev` and `agent-lab scenario run` do not spawn separate processes:
they boot the real backend app and the real MockWorld app with uvicorn **in
the same asyncio loop** as the CLI (`cli/src/agentlab/cli/servers.py`), wait
for `/openapi.json` readiness, and shut both down on exit. No containers, no
docker-compose.

**One world engine per process.** MockWorld holds a process-global SQLModel
engine over a single SQLite file (`AGENTLAB_DB`, default `./agent-lab.db`,
WAL mode — DEC-02). A per-process `asyncio.Lock` serializes the mutating
simulation calls (`POST /simulation/reset`, `/load`, `/mutate`) so concurrent
scenario runs sharing one world cannot race a wipe/seed. The lock is a
*serialization*, not isolation: two runs still share one world, and the unit
of isolation is the process.

## Auth and identity

This is DEC-09's MVP token layer ([PROVISIONAL — security-relevant]), not
production auth. Know exactly what each mechanism does:

- **Agent identity: `X-Agent-Id` header.** Required on the backend's
  agent-facing workflow routes (missing → 401 `UNAUTHORIZED`) and on every
  MockWorld `/world/*` route. There is no token or signature behind it — the
  header value *is* the identity.
- **Registered-agent enforcement.** `POST /events` rejects callers not in the
  channel hub registry (404). MockWorld enforces registration server-side
  (DEC-07): `ALLOWED_DOMAINS` (`agent-id:domain` pairs, default
  `device-agent:devices`) gates the shared `/world/employees/{id}` route to
  known agents and each domain router (`/world/devices`, `/world/access`, …)
  to agents registered for that domain (else 403 `FORBIDDEN`).
- **Actor forcing.** On `POST /events` the backend forces `actor` to the
  `X-Agent-Id` value and sets `ts` server-side; client-supplied values for
  either are ignored, and `type` is validated against the `EventType`
  vocabulary. Agents cannot spoof each other's events.
- **Privileged simulation endpoints.** `/simulation/*` requires
  `Authorization: Bearer <SIMULATION_TOKEN>` (default `dev-token`; the
  ScenarioEngine sends its configured token, from its constructor argument or
  `AGENTLAB_SIMULATION_TOKEN`). Anything else → 401 `UNAUTHORIZED`. These
  endpoints can wipe and rewrite the world; the token is the only barrier,
  and the default is public knowledge — treat the world process as trusted
  network only.
- **Human-task decisions (DEC-10).** `POST /tasks/{id}/decision` rejects a
  resolver other than the task's `requested_from` with 403
  `UNAUTHORIZED_APPROVER`, unless `ALLOW_ANY_RESOLVER=1` is set (a test
  escape hatch — never set it where the unauthorized-approver scenarios
  matter).
- **CORS.** Both apps allow only the vite dev origins
  (`http://localhost:5173`, `http://127.0.0.1:5173`) with
  `allow_credentials=True` and wildcard methods/headers. See migration notes
  for tightening.

## Known limits (honest list)

- **No scenario-run-over-HTTP.** `GET /scenarios` is a read-only disk listing
  (full `expected` block for team packs; minimal id/file metadata for hidden,
  per DEC-14). Running a scenario happens via `agent-lab scenario run` or the
  pytest certification packs. The route's own docstring marks a run endpoint
  as deferred.
- **Evaluation results are not persisted.** `GET /evals/model` returns only
  the evaluation *inventory* — the SPEC §24 weights, threshold, pass
  criterion, and pack contents — computed live from the scoring constants and
  the YAMLs on disk. There is no eval-results table and no run history API.
- **One world engine per process.** The SQLite engine, the in-memory
  `ACTIVE_FAULTS` registry, and the simulation mutation lock are all
  process-global. Horizontal scaling of the world is not a configuration
  change; it is an architecture change (below).
- **Hidden scenarios never run in CI.** `scenarios/hidden/` is gitignored
  (DEC-14); the hidden runner skips on any host without the private archive.
  CI green says nothing about the hidden scenarios — that signal exists only
  on the platform host with `AGENTLAB_HIDDEN_DIR` pointed at the archive.
- **SQLite single-writer.** WAL mode gives concurrent readers one writer.
  The backend and MockWorld share one file, and simulation mutations already
  serialize through a lock; under real write concurrency SQLite is the first
  contention point.
- **The scripted CLI trajectory is narrow.** `agent-lab scenario run` covers
  the device happy path; everything else runs through the pytest packs.
- **No LLM in the lab itself.** All certification and integration runs are
  deterministic (scripted trajectories, canned model turns). Live-LLM agent
  runs are the participant's own `agent-lab dev` loop, not an evaluated
  surface.

## Migration notes: single-process → multi-process/multi-host

The SPEC §30 mapping (Markdown→Confluence, MockWorld→enterprise APIs,
channels→Slack, SQLite→durable store, local ADK→managed runtime) holds
because agents depend only on abstract interfaces. What breaks first is not
the agents — it is the in-process singletons:

1. **The WebSocket channel hub** (`backend/.../hub.py`). The agent registry
   and live sockets are in-memory in one backend process. A second backend
   replica splits the registry: `POST /events`'s registered-agent check and
   `GET /agents` disagree across replicas, and hello/welcome handshakes only
   reach the replica that holds the socket. Move the registry to shared
   storage before scaling the backend.
2. **The world engine.** The process-global SQLite engine, the mutation lock,
   and `ACTIVE_FAULTS` do not cross processes. Two world processes over one
   file lose the serialization guarantee (the lock is per-process); the fix
   is a single world writer or a real database, not more replicas.
3. **The channel hub's message surface.** Channel history is persisted, but
   the hub's live fan-out is in-memory; a multi-host deployment needs an
   external bus (the SPEC's Slack mapping).

What survives the move unchanged:

- **The Event Store is the audit surface.** Every state transition and agent
  event is an append-only row correlated by `case_id` / `workflow_id`
  (SPEC §19/§23). Migrating the store (DEC-02's "migration path" rationale
  for SQLModel) preserves the trace timeline as the system of record.
- **The scenario/evaluation machinery.** The engine drives MockWorld over
  HTTP only and never touches the backend or SQLite directly; pointing
  `MOCKWORLD_URL` at a remote world is sufficient. This is the piece SPEC
  §30 calls the durable asset (the pre-production assurance harness).

**CORS tightening for production.** Today both apps hard-code the vite dev
origins with wildcard methods/headers and credentials allowed. Before
exposing either service beyond loopback: pin `allow_origins` to the deployed
console origin, drop `allow_credentials` or scope it, enumerate the actual
methods/headers, and revisit DEC-09 — the bearer token and bare `X-Agent-Id`
are explicitly the MVP layer, with SSO/IAM deferred by SPEC §29.

## Observability

The **trace timeline is the debugging foundation** (SPEC §23): a filtered,
chronological query over the Event Store's append-only rows. Every case's
full history — case creation, delegation, acknowledgements, blockers, human
tasks, approvals, verification, completion/failure, plus whatever agents
record through `POST /events` — lands in one table.

- **Event types** are the canonical `EventType` vocabulary
  (`sdk/src/agentlab/sdk/events.py`): `CASE_CREATED`,
  `WORKFLOW_DELEGATED`, `WORKFLOW_ACKNOWLEDGED`, `TOOL_CALL`, `TOOL_RESULT`,
  `KNOWLEDGE_READ`, `BLOCKER_CREATED`, `HUMAN_TASK_CREATED`,
  `APPROVAL_GRANTED`, `APPROVAL_REJECTED`, `OUTCOME_VERIFIED`,
  `WORKFLOW_COMPLETED`, `WORKFLOW_FAILED`, `ESCALATED`.
- **Where audit evidence lives**: the shared SQLite file (`AGENTLAB_DB`,
  default `./agent-lab.db`), in the backend-owned event/case/workflow/task
  tables. Everything correlates with `case_id` and `workflow_id`.
- **How to read it**: `GET /cases/{case_id}/events` (also what the console's
  Trace view renders), or `agent-lab trace --case <id>` against a running
  backend.
- **Scenario-run evidence** lives in the run's `ScenarioResult`: the engine
  timeline (reset, load, each mutation, each armed fault, agent start/end),
  the observed trajectory events, the final state, and `faults_applied` — the
  record of which injected faults actually fired. During evaluation these are
  asserted deterministically (SPEC §24); they are not yet persisted anywhere
  (see Known limits).

Operational rule of thumb: when a run misbehaves, read the case timeline
first. If the answer is not in the events, the instrumentation gap is a bug —
every event write goes through the single helper, so extend it there.
