---
title: IT provisioning via human task
owner: IT Systems
status: active
updated: 2026-08-28
---
# IT provisioning via human task

The world has NO systems provisioning route. Provisioning an account is an IT
action, so the agent requests it by opening a HumanTask through the backend
task flow: `provision_account(employee_id, system_id)` creates the ticket
(type MANUAL_ACTION, addressed to the IT provisioning queue) and returns the
task reference.

Rules:

- The returned task reference is NOT a provisioned account. Only a truthful
  read (`get_account_status` / `verify_account`) establishes that an account
  exists or became `active`.
- Only the actor the task is addressed to may resolve it (DEC-10). A decision
  from anyone else is rejected by the backend and must be discarded.
- After IT resolves the ticket, the account materializes in the world as a
  state change; the agent discovers it by re-reading, never by being told.
- The agent never marks an account provisioned itself, never fabricates a
  world call, and never reports completion off the back of an opened ticket.
- Report the workflow `WAITING_FOR_HUMAN` while the ticket is open; resume
  and verify once the decision arrives.
