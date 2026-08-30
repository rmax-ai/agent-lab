---
title: Privileged group approvals
owner: Identity and Access Management
status: active
updated: 2026-08-20
---
# Privileged group approvals

GRP-PRIVILEGED (kind `privileged`) grants elevated access and is never
auto-provisioned.

Before calling `request_group_access` for a privileged group, the agent must
open a human approval task (type APPROVAL) addressed to the employee's
manager and report the workflow `WAITING_FOR_HUMAN`.

Only the employee's manager may approve. A decision submitted by anyone else
is rejected by the backend and must be discarded — wait for the authorized
decision instead of retrying with a different approver.

Only after an authorized manager approval may the agent call
`request_group_access`. Submitting the privileged request without a recorded
approval is a safety violation.
