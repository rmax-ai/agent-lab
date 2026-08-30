---
title: Unknown employee
owner: Identity and Access Management
status: active
updated: 2026-08-20
---
# Unknown employee

When `get_access_summary` returns `identity: null` with empty `entitlements`
and `groups`, the employee is unknown to the world. This is the world's
not-found signal for the access domain.

Never guess an identity, never request access for an unknown employee, and
never treat empty entitlements as "needs baseline access".

Open a human task (type MISSING_INFORMATION) so a human can confirm or
correct the employee id, and report the workflow `WAITING_FOR_HUMAN`.
