---
title: Systems escalation
owner: IT Systems
status: active
updated: 2026-08-28
---
# Systems escalation

Escalate — report a blocker and open a human task, then report the workflow
`BLOCKED` or `WAITING_FOR_HUMAN` — when any of these holds:

- An IT provisioning ticket is never resolved within the human-task SLA, or
  IT rejects it and the rejection conflicts with the employee's role.
- A required account stays `pending` past the provisioning deadline (stuck
  account): escalate to IT with the system id; never report it complete.
- Provisioning tool calls keep failing past the bounded-retry budget (see
  `service-degradation.md`).
- An SYS-HR account exists for a non-manager (see `hr-system-policy.md`):
  escalate for deprovisioning; the violation blocks completion.

Every escalation carries a machine-readable blocker code (for example
`provisioning_unavailable`, `account_provisioning_stuck`,
`hr_account_for_non_manager`) plus the employee id, the system id, and the
policy citation, and it involves the onboarding coordinator. Never improvise
a workaround and never complete a workflow with an open blocker.
