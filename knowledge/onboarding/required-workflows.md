---
title: Required workflows by profile
owner: Platform Team
status: active
updated: 2026-08-20
---
# Required workflows

The coordinator delegates one workflow per required outcome. The required set
derives from the case profile, with an explicit override available.

| Profile | Required goals |
| --- | --- |
| `default` | device, access, systems, applications |
| `contractor` | device, access |

Resolution order:

1. An explicit `workflows` argument (for example `["device", "access"]`).
2. A `workflows` key in the case `context` (for example `{"workflows": ["device", "access"]}`).
3. A `profile` key in the case `context`.
4. The `default` profile (all four goals).

Each goal maps to its owning domain agent:

| Goal | Domain agent |
| --- | --- |
| `employee_device_ready` | `device-agent` |
| `employee_access_ready` | `access-agent` |
| `employee_systems_ready` | `systems-agent` |
| `employee_applications_ready` | `applications-agent` |
