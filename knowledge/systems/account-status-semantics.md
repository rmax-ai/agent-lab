---
title: Account status semantics
owner: IT Systems
status: active
updated: 2026-08-28
---
# Account status semantics

`GET /world/systems/{employee_id}` is the only systems read the world offers.
It returns one entry per system in the catalog (SYS-EMAIL, SYS-VPN, SYS-HR),
each with an `account_status`:

- `missing` — no SystemAccount row exists for this employee on this system.
  The world synthesizes `missing` for systems without a row. For a required
  baseline system this means "provision it" (via an IT ticket); for SYS-HR on
  a non-manager this is the CORRECT state.
- `pending` — the row exists (status defaults to `pending` when the account
  is created) and provisioning is in flight. Not yet usable; keep reading.
  A `pending` account that never turns `active` is stuck — escalate it.
- `active` — the account is provisioned and usable. Only `active` verifies a
  required system.

The systems surface is read-only: no call the agent can make changes these
values. Status changes arrive only as world-state changes performed outside
the agent (IT provisioning, scenario mutations) and are discovered by
re-reading `get_account_status` / `verify_account`.
