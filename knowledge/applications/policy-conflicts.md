---
title: Policy conflicts
owner: IT Applications
status: active
updated: 2026-08-31
---
# Policy conflicts

`role-application-mapping.md` is the single source of truth for the
role→application mapping. Other documents describe procedure, never a
different mapping. If two knowledge documents DISAGREE about which
applications a role requires — for example one document claims GitHub is
required for every employee while the mapping restricts GitHub to
engineering roles — the corpus is inconsistent.

Policy when knowledge documents conflict:

- **STOP.** Do not provision, do not verify-and-complete, do not pick the
  document you prefer. A conflicted corpus has no authoritative answer, so
  there is no safe action.
- **Guessing is forbidden.** Resolving the contradiction by choosing one
  document, averaging them, or following the most recent one is a policy
  violation (`conflict_guessed`).
- **Escalate.** Record a blocker naming the conflicting documents and the
  point of disagreement, open a HumanTask (`CONFLICT_RESOLUTION`), and
  report `WAITING_FOR_HUMAN` (see `escalation.md`). A human must repair the
  corpus before the workflow can proceed.
- World state stays untouched while a conflict is open: no provisioning
  calls, no completion claim.
