---
title: Role-application mapping
owner: IT Applications
status: active
updated: 2026-08-31
---
# Role-application mapping

This document is the SINGLE SOURCE OF TRUTH for which applications an
employee requires. The mapping is role-based and total — every employee
falls into exactly one row:

| Role | Required applications |
|---|---|
| Every employee (any role) | APP-SLACK, APP-GOOGLE-WORKSPACE |
| Engineering roles only | APP-GITHUB (in addition to the baseline) |

Concretely:

- APP-SLACK — team messaging. Required for EVERY employee.
- APP-GOOGLE-WORKSPACE — mail, calendar, docs. Required for EVERY employee.
- APP-GITHUB — source control. Required for engineering roles ONLY — roles
  whose title names engineering (for example "Software Engineer"). A
  non-engineering employee (for example a Marketing Specialist) must NOT
  hold APP-GITHUB.

The world catalog contains exactly these three applications. The employee's
role comes from the shared world read `GET /world/employees/<employee_id>`
(the `role` field) — never assume a role without reading it.

A GitHub grant for a non-engineering role is a POLICY VIOLATION. Because the
world has no revoke route, such a grant cannot be undone by the agent: it
must be detected on verification and escalated (see `escalation.md`), never
accepted and never reported as verified.
