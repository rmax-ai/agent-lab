# Phase 1 Research Findings — ADK v2 + Python Stack

> Date: 2026-08-29 · Sources: pre-researched ADK reference (verified ≤2.3.0, June 2026) + PyPI primary-source version pins

## 1. Version pins (PyPI, 2026-08-29)

| Package | Version | Role |
|---|---|---|
| google-adk | 2.8.0 | Agent runtime (LlmAgent, InMemoryRunner, Workflow) |
| google-genai | 2.20.0 | Gemini client (ADK dependency) |
| fastapi | 0.141.1 | Backend + MockWorld |
| uvicorn | 0.52.4 | ASGI server (WS support) |
| pydantic | 2.13.5 | Schemas |
| sqlmodel | 0.0.42 | SQLite ORM (decision DEC-02) |
| websockets | 17.1 | WS library |
| httpx | 0.28.1 | Tool HTTP client |
| pytest / pytest-asyncio | 9.1.1 / 1.4.0 | Tests |
| ruff | 0.16.5 | Lint |
| ty | 0.0.75 | Type check |
| pyyaml | 6.0.3 | Scenario definitions |

## 2. ADK v2 API shape (verified ≤2.3.0; RE-VERIFY against 2.8.0 at Phase A)

Key facts the implementation must respect:

- `from google.adk import Agent` IS `LlmAgent`. Import `LlmAgent` explicitly.
- `InMemoryRunner(agent=..., app_name=...)` — v2.2.0 removed the `session_service=` constructor param.
- **`auto_create_session` defaults to `False` since v2.3.0** — set `runner.auto_create_session = True` or create sessions explicitly, else `SessionNotFoundError`.
- `run_async(user_id, session_id, new_message=Content(...))` expects `google.genai.types.Content`, not dicts. Events use `event.partial` (skip streaming chunks), `event.is_final_response()` for the answer.
- Resumption requires the SAME runner instance (its session service is internal).
- Default model changed to `gemini-3-flash-preview` in v2.2.0 — **pin the model explicitly** in the template; make it env-configurable.
- Tools: plain async functions with docstrings are auto-wrapped as `FunctionTool`. `ToolContext` auto-injected.
- No built-in fake model — use `before_model_callback` returning canned `LlmResponse` for deterministic agent tests.
- `CallbackContext` does not exist — it's `Context` from `google.adk.agents.context`.
- SequentialAgent/ParallelAgent/LoopAgent deprecated → use `Workflow` graph. Not needed for Agent Lab MVP (our multi-agent coordination is via channels + workflow contract, not ADK Workflow — DEC-04).
- Gemini structured output is unreliable for nested Pydantic models — keep schemas FLAT; fallback to text-parse. Directly relevant to the WorkflowRequest/Status/Outcome JSON contract (SPEC §12).

### Verified against installed google-adk 2.8.0 (2026-08-29, scripts/verify_adk_280*.py)

1. **`auto_create_session` defaults to `False`** in 2.8.0 (confirmed at runtime) — the v2.3.0 behavior persists. Always set `runner.auto_create_session = True` or create sessions explicitly.
2. **`InMemoryRunner(agent=..., app_name=...)`** — no `session_service=` param. Params: `agent, node, app_name, plugins, app, plugin_close_timeout`.
3. **No built-in FastAPI/WS serving modules** (`google.adk.serving/fastapi/web` all ModuleNotFoundError; only `google.adk.cli` exists). Remote/laptop agents MUST be wrapped in our own FastAPI/WebSocket adapter exposing AgentLabTransport — which was the plan anyway (DEC-12).
4. **Callbacks exist but NOT as `__init__` params** — they are Pydantic model fields on `LlmAgent`: `before_tool_callback`, `after_tool_callback`, `on_tool_error_callback`, `before_model_callback`, `after_model_callback`, `on_model_error_callback`, `before_agent_callback`, `after_agent_callback`. Pass via kwargs or assign post-construction (`agent.before_tool_callback = ...`). Fault injection (A.11) and deterministic model mocks (A.3) both work through these.
5. **2.8.0 additions:** `LlmAgentConfig`/`BaseAgentConfig` classes (config-object construction pattern), `ManagedAgent`, `McpInstructionProvider`, `LiveRequestQueue`. `FunctionTool(func, require_confirmation=...)` — tool-level confirmation flag exists.
6. **`InMemorySessionService`** unchanged: `create_session`, `get_session`, `append_event`, etc.

### Notes for Droid stories A.2/A.3

- Deterministic agent tests: `before_model_callback` returning canned `LlmResponse` (verified field path).
- Scenario fault injection: `before_tool_callback`/`after_tool_callback` on the agent instance (verified field path).
- Session state keys (`app:`, `user:`, `temp:`) and `Context` (not `CallbackContext`) per the ≤2.3.0 reference — unchanged in 2.8.0 (imports verified).

## 3. Decisions informed by research

- **Prompt-injection protection (THREAT_MODEL T-01):** adopt the DATA_DELIMITER pattern — knowledge documents are rendered into context under an explicit "DATA, NOT INSTRUCTIONS" delimiter inside `MarkdownKnowledgeProvider`. Knowledge stays advisory; tools are the only world access (SPEC §1).
- **Deterministic agent tests:** `before_model_callback` mock + canned responses; no real LLM calls in pytest.
- **Serial LLM latency:** Onboarding Agent delegating to 4 domain agents must run domain workflows concurrently (async, per-workflow). Gemini may serialize parallel calls from one process — architect for parallelism regardless (provider-agnostic).
- **Model pinning:** `AGENTLAB_MODEL` env var; template pins an explicit default. No reliance on ADK defaults (they migrate).

## 4. Remaining spec-silent questions (from THREAT_MODEL) — resolved or assigned

| # | Question | Disposition |
|---|---|---|
| 1 | Auth for /world/*, /simulation/* | DEC-09: provisional shared bearer token + per-agent registration tokens; SSO deferred per SPEC §29. NEEDS MAX CONFIRMATION |
| 2 | Approval authority detection | DEC-10: approver identity carried on HumanTask (`requested_from`); unauthorized-approval scenarios simulate via event injection; enforcement provisional |
| 3 | Numeric bounds (retries, depth, SLA) | DEC-08: max_retries=3, delegation_depth=2, tool_timeout=30s, HITL no-response=300s→escalate |
| 4 | Server-side per-domain tool enforcement | DEC-07: MockWorld validates caller agent identity against allowed tool/endpoint map |
| 5 | Fault semantics | DEC-05: faults applied only to declared mutation tools; `verify_*` reads stay truthful (otherwise verification is meaningless) |
| 6 | Hidden scenario mechanics | DEC-11: excluded from public repo entirely; live in private platform archive |
| 7 | Concurrency model | DEC-06: per-case event isolation via case_id correlation; WS sessions keyed by agent identity; 12-starter finals run as concurrent workflows in one backend process |
