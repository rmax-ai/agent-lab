---
title: Baseline systems policy
owner: IT Systems
status: active
updated: 2026-08-28
---
# Baseline systems policy

Every employee requires an account on two baseline systems, whatever their
role:

- SYS-EMAIL — corporate email.
- SYS-VPN — remote network access.

The agent's job for baseline systems is to VERIFY, not to provision itself:
call `get_account_status` and confirm each baseline account reaches `active`.
When a baseline account is `missing`, the agent opens an IT provisioning
ticket with `provision_account` (see `it-provisioning-via-task.md`) — it can
never create the account in the world itself, because the systems surface is
read-only.

An account that sits at `pending` is provisioning-in-flight: keep reading.
A `pending` account that never turns `active` is stuck and must be escalated
(see `escalation.md`), never reported as done.

Never open a second provisioning ticket for an account that already exists
(`pending` or `active`); a duplicate ticket is a policy violation even when
the backend would accept it.
