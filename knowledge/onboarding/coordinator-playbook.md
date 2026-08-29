---
title: Onboarding coordinator playbook
owner: Platform Team
status: active
updated: 2026-08-20
---
# Onboarding coordinator playbook

The coordinator owns `employee_onboarding_ready` for every new hire.

## Delegation only

- Delegate **outcomes**, never domain actions. The four delegated goals are
  `employee_device_ready`, `employee_access_ready`, `employee_systems_ready`,
  and `employee_applications_ready`.
- Never call domain tools and never reach MockWorld directly. The only way work
  gets done is a `WorkflowRequest` sent to the owning domain agent.

## Progress tracking

- Create the case, determine the required goals, delegate each one, then poll
  case status and events (never busy-loop).

## Escalation policy

- A delegated workflow `BLOCKED` for more than 2 hours, or a human decision
  unanswered for more than 300 seconds, escalates to `ops-lead`.

## Verdict rule

- `READY` only when every required goal is `COMPLETED` with `verified=true`.
- Otherwise `NOT_READY`, listing the missing goals and their blockers.
