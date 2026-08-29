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

### Open items to verify at Phase A (against installed google-adk 2.8.0)

1. `auto_create_session` default and any constructor changes since 2.3.0.
2. ADK's built-in FastAPI/WebSocket serving mode for remote (laptop) agents — is `adk`'s serve API usable as-is, or do we wrap LlmAgent in our own FastAPI app exposing our AgentLabTransport WS protocol? (Preferred: our own WS adapter to keep AgentTransport swappable.)
3. Tool fault injection points — `before_tool_callback`/`after_tool_callback` are the natural fault-injection seams (SPEC §17.2).
4. pytest 9.1 + pytest-asyncio 1.4 compatibility (asyncio_mode config).

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
