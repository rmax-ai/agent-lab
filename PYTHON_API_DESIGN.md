# PYTHON_API_DESIGN.md — Schemas & Interface Design

All wire formats use the field names from SPEC.md **verbatim** (snake_case): `workflow_id`, `case_id`, `employee_id`.

## Protocol models (sdk/protocols.py)

SPEC §12 contract, flat Pydantic v2 models (DEC-16 — no nested model-of-model lists; Gemini can't be trusted with deep schemas):

```python
class WorkflowRequest(BaseModel):
    workflow_id: str
    case_id: str
    goal: str                      # e.g. "employee_device_ready"
    employee_id: str
    context: dict[str, Any]        # open by design (start_date etc.)

class Blocker(BaseModel):
    code: str                      # e.g. "NO_INVENTORY"
    description: str

class WorkflowStatus(BaseModel):
    workflow_id: str
    status: WorkflowState          # enum: ACKNOWLEDGED RUNNING BLOCKED WAITING_FOR_HUMAN FAILED COMPLETED
    blockers: list[Blocker] = []

class WorkflowOutcome(BaseModel):
    workflow_id: str
    status: WorkflowState
    verified: bool
```

- `context` stays open (teams pass domain data); everything else `extra="forbid"`.
- `WorkflowState` is a `str, Enum` — serialize to/from the exact strings above.

## Event model

```python
class Event(BaseModel):
    ts: datetime          # UTC, ISO 8601
    case_id: str
    workflow_id: str | None
    actor: str            # agent id | "human" | "mockworld" | "scenario"
    type: str             # CASE_CREATED, TOOL_CALL, ... (SPEC §23)
    payload: dict[str, Any]
```

Append-only. The trace timeline is a filtered query on this table.

## HumanTask (SPEC §15)

Fields: `human_task_id, case_id, workflow_id, requested_by, requested_from, type, context, allowed_actions, status, decision, resolved_by, timestamps`. Type enum: APPROVAL, MISSING_INFORMATION, CONFLICT_RESOLUTION, EXCEPTION_HANDLING, MANUAL_ACTION.

## FastAPI routes

- `/world/*` — agent-facing. Separate router, requires agent registration token, **enforces per-domain tool identity** (DEC-07). Responses: Pydantic response models; 409 with `Blocker` body for business rejections.
- `/simulation/*` — privileged. Separate router, separate auth (DEC-09). Never imported by, wrapped by, or referenced in any tool module.
- `/cases/*`, `/channels/*`, `/tasks/*`, `/scenarios/*`, `/evals/*` — platform/UI routes.

## Tool signatures (ADK function tools)

```python
async def check_inventory(employee_id: str) -> dict:
    """Check available inventory against the employee's device requirements."""
    ...
```

- Plain async functions. Docstring = tool description shown to the model.
- Return dicts (flat). Tools NEVER return raw HTTP errors — they translate to structured results the agent can reason about.
- `verify_*` tools return truthful world state (DEC-05).

## Error envelope

`{"error": {"code": str, "description": str}}` everywhere; codes reuse blocker vocabulary where applicable.

## Naming

- Modules per SPEC §28 layout; package names `agentlab.*`.
- Tool names = spec §10 names exactly (`check_inventory`, `request_entitlement`, ...).
- Events use the SPEC §23 vocabulary exactly.
