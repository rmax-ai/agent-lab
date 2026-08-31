# Systems domain knowledge

Reference corpus for the systems agent (goal `employee_systems_ready`).

- `baseline-systems.md` — SYS-EMAIL + SYS-VPN for every employee, and the verify-not-provision rule.
- `hr-system-policy.md` — SYS-HR is for people managers only; a violation must be detected and escalated.
- `account-status-semantics.md` — what `missing`, `pending`, and `active` mean on the read-only world surface.
- `it-provisioning-via-task.md` — provisioning is a HumanTask to IT; only truthful reads establish state.
- `service-degradation.md` — bounded retries on tool errors/timeouts, then escalate; never infinite retry.
- `escalation.md` — when and how to escalate unresolvable systems blockers.
