# Applications domain knowledge

Reference corpus for the applications agent (goal `employee_applications_ready`).

- `role-application-mapping.md` — the single source of truth for the
  role→application mapping: Slack + Google Workspace for everyone, GitHub
  only for engineering roles.
- `provisioning-policy.md` — provision what is required and missing; the
  grant route is idempotent; there is NO revoke route.
- `unknown-applications.md` — 404 `NOT_FOUND` semantics: detect, escalate,
  never guess an application id.
- `access-failure-policy.md` — bounded retries on tool faults, then escalate;
  never infinite retry.
- `policy-conflicts.md` — when knowledge documents disagree, STOP and
  escalate a blocker; guessing is forbidden.
- `escalation.md` — when and how to escalate unresolvable applications
  blockers.
