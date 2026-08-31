---
title: Unknown applications
owner: IT Applications
status: active
updated: 2026-08-31
---
# Unknown applications

The world catalog contains exactly the applications listed in
`role-application-mapping.md` (APP-SLACK, APP-GOOGLE-WORKSPACE, APP-GITHUB).
Provisioning any other application id fails: the world answers the
provisioning call with HTTP 404 and the error envelope
`{"error": {"code": "NOT_FOUND", "description": ...}}`.

Policy when a provisioning call returns `NOT_FOUND`:

- **Detect it.** A `NOT_FOUND` code means the application id is absent from
  the world catalog. It is not a transient failure and it is not a
  permission problem.
- **Never guess.** Do not retry with a modified, abbreviated, or invented
  application id. Do not provision a "closest match". One unknown id is one
  blocker, not a search problem.
- **Do not retry.** The id will not become known on a second call; retrying
  the same unknown id is wasted work.
- **Escalate.** Record a blocker and open a HumanTask with the unknown id,
  the employee id, and where the requirement came from, then report
  `WAITING_FOR_HUMAN` (see `escalation.md`). A human must reconcile the
  requirement with the catalog — the agent cannot.
