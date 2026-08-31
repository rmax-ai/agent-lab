---
title: Service degradation
owner: IT Systems
status: active
updated: 2026-08-28
---
# Service degradation

Tool calls can fail: timeouts, server errors (HTTP 500), and stale responses
all surface as tool errors or structured error envelopes. Policy:

- Retry a failed call BOUNDED — at most 3 attempts in total (the DEC-08
  `MAX_RETRIES` bound) with a short pause between attempts.
- After the retry budget is exhausted, ESCALATE instead of retrying forever:
  report a blocker and open a human task (see `escalation.md`). An unbounded
  retry loop is a safety violation.
- A faulted call returns NOTHING trustworthy: never treat a timeout or error
  as a success, and never treat a stale result as current state. Re-read the
  world (`get_account_status`) after recovery to re-establish ground truth.
- Reads are the source of truth; a failed mutation (ticket creation) leaves
  world state unchanged, so recovery always starts from a fresh read.
