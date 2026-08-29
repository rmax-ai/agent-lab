---
title: Human-in-the-loop policy
owner: Platform Team
status: active
updated: 2026-08-20
---
# Human-in-the-loop policy

The coordinator opens a human task when a delegated workflow is `BLOCKED` and
needs a decision, or when stalled work must be escalated.

- `requested_by` is `onboarding-agent`.
- `requested_from` is `ops-lead`.
- `allowed_actions` are `approve` and `reject`.

`ops-lead` resolves the task with `POST /tasks/{id}/decision`. On `approve` the
coordinator re-delegates the goal with a fresh `workflow_id`; on `reject` the
goal is reported as not ready. If the decision is not answered within 300
seconds, the coordinator escalates by failing the workflow with reason
`hitl_timeout`.
