---
title: Escalation
owner: IT Applications
status: active
updated: 2026-08-31
---
# Escalation

Some blockers cannot be resolved by the agent. Escalate — report a blocker
and open a HumanTask, then report the workflow `WAITING_FOR_HUMAN` — when
any of these holds:

- **Unknown application.** A required application id is absent from the
  world catalog (404 `NOT_FOUND`). See `unknown-applications.md`.
- **Provisioning unavailable.** The provisioning tool kept failing past the
  bounded retry budget. See `access-failure-policy.md`.
- **Policy conflict.** Knowledge documents disagree about the required
  applications. See `policy-conflicts.md`.
- **Out-of-role grant.** Verification found a grant the role→application
  mapping forbids (for example GitHub held by a non-engineering employee).
  There is no revoke route, so only a human can repair this.

How to escalate:

1. Stop provisioning and stop verifying-toward-completion.
2. Open a HumanTask with machine-readable context: the employee id, the
   application id (when relevant), the policy citation, and what the agent
   observed. Address it to the responsible human actor.
3. Report the workflow `WAITING_FOR_HUMAN` (or `BLOCKED`) with the same
   reason. Never report `COMPLETED` while an escalation is unresolved.
4. Only the actor the task is addressed to may resolve it; never proceed on
   anyone else's decision, and never resolve your own task.
