# PYTHON_DEVELOPMENT.md — Python Engineering Conventions

Day-to-day idioms for Python work in this repo. Version pins: `docs/research/phase-1-findings.md`.

## Environment

- Python 3.12+. `uv sync` per workspace member; root `uv run` dispatches.
- Secrets via `.envrc` + `direnv` (`GOOGLE_API_KEY`, `AGENTLAB_MODEL`, tokens). Never in code, never committed. See `direnv-project-secrets` skill.

## Gates (run before every PR)

```bash
uv run ruff check
uv run ty check
uv run pytest
```

All green before merge. Pin ruff/ty/pytest to the versions in phase-1-findings.

## Async & FastAPI

- Single event loop per backend process. Async endpoints throughout.
- SQLite (SQLModel) is sync — wrap DB calls in `asyncio.to_thread`; WAL mode on.
- Long agent runs are event-loop driven; never `time.sleep` inside coroutines — use `asyncio.sleep`.
- WebSockets: one connection per agent, keyed by agent identity. Reconnect with backoff. Heartbeat ping.

## Error handling

- Domain errors become **typed blockers**, not exceptions across the wire: `{"code": "NO_INVENTORY", "description": "..."}` (SPEC §12).
- Exceptions: log + emit a `WORKFLOW_FAILED` event with the reason; never leak tracebacks into channels.
- HTTP errors from MockWorld: map 409 → blocker with code; 500 → retry per DEC-08 bounds.

## Logging & events

- All observable actions go through the Event Store helper (SPEC §23). Rule: if it appears on the trace timeline, it must be an event row; no ad-hoc prints.
- Event types: CASE_CREATED, WORKFLOW_DELEGATED, TOOL_CALL, TOOL_RESULT, KNOWLEDGE_READ, BLOCKER_CREATED, HUMAN_TASK_CREATED, APPROVAL_GRANTED, OUTCOME_VERIFIED, ...

## ADK usage patterns

- `LlmAgent` with explicit `model=` (never default). Plain async functions as tools; docstring = tool description.
- In-process agents: `InMemoryRunner`, remember `runner.auto_create_session = True` (v2.3.0+ default False) and `new_message=Content(role="user", parts=[Part(text=...)])`. Events: skip `event.partial`, read `event.is_final_response()`.
- **Deterministic tests only:** mock the model via `before_model_callback` returning canned `LlmResponse`. No real LLM calls in pytest. See `references/adk-fake-model-testing.md` in the fp skill if lost.
- Tool fault injection (SPEC §17.2) hooks into `before_tool_callback` / `after_tool_callback` — the scenario engine wraps tool calls there.

## Testing

- pytest + pytest-asyncio (`asyncio_mode = auto`). Verify pytest 9.1 + pytest-asyncio 1.4 compat at Phase A.
- **Unique test basenames across packages** — `test_smoke.py` in four packages breaks rootdir collection; use `test_<pkg>_<name>.py`.
- Unit: state machine transitions, scenario parser, evaluator assertions, MockWorld routes.
- Integration: scenario runs against a scripted agent (mock model), asserting `required_events` / `allowed_final_states` / `forbidden_events`.
- Eval tests are data-driven: one pytest param per scenario YAML.

## Style

- Full type hints; `ty` strict. Pydantic models for all wire/DTO shapes.
- Docstrings on every tool function (ADK uses them as tool descriptions).
- ruff format applied; no manual formatting debates.
