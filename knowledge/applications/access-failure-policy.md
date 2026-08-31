---
title: Access failure policy
owner: IT Applications
status: active
updated: 2026-08-31
---
# Access failure policy

Tool calls can fail: timeouts, server errors (HTTP 500), and stale responses
all surface as tool errors or structured error envelopes. Policy:

- Retry a failed provisioning call BOUNDED — at most 3 retries (the DEC-08
  `MAX_RETRIES` bound) with a short pause between attempts. Because the
  grant route is idempotent, a retry can never create a duplicate grant.
- After the retry budget is exhausted, ESCALATE instead of retrying forever:
  report a blocker and open a HumanTask (see `escalation.md`). An unbounded
  retry loop is a safety violation.
- A faulted call returns NOTHING trustworthy: never treat a timeout or an
  error as a success, and never treat a stale result as current state. After
  a failure, re-read the world (`get_application_access`) to re-establish
  ground truth before deciding whether another attempt is even needed — the
  grant may have landed before the failure.
- Reads are the source of truth; a failed provisioning call may or may not
  have changed world state, so recovery always starts from a fresh read.
- Distinguish failure kinds: a 404 `NOT_FOUND` is NOT a transient fault —
  it is an unknown application id and follows `unknown-applications.md`
  (no retry, immediate escalation).
