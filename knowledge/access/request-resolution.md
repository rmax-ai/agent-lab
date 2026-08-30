---
title: Request resolution
owner: Identity and Access Management
status: active
updated: 2026-08-20
---
# Request resolution

A new access request starts in status `requested`. The world's IAM backend
resolves it asynchronously: an approved request becomes `granted`, a denied
one becomes `denied`.

Track resolution with `list_access_requests` until the request leaves
`requested`.

An approved (`granted`) request completes the access goal for that group.

A denied request is final for the stated justification: do not retry the
same request without new information. If the denial looks wrong, escalate
instead of resubmitting.
