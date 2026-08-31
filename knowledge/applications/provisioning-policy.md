---
title: Provisioning policy
owner: IT Applications
status: active
updated: 2026-08-31
---
# Provisioning policy

Provisioning means granting an application through the world's provisioning
route `POST /world/applications/<employee_id>/provision`.

Rules:

- **Provision what is required and missing.** Compute the required set from
  `role-application-mapping.md`, read the current grants with
  `get_application_access`, and provision exactly the required applications
  whose grant is absent. Nothing more.
- **Check before you provision.** A grant that already exists needs no
  action. Re-provisioning an already-granted application is wasted work and,
  as a pattern, a policy violation (duplicate provisioning) — the read tells
  you the truth, so read first.
- **Idempotent re-grant is safe.** The route is idempotent: if a grant
  already exists, the world re-marks the same row `granted`. A retry after
  an uncertain failure (for example a timeout where the grant may have
  landed) is therefore safe — re-read first, and re-grant only if the read
  still shows the grant missing.
- **There is NO revoke route.** The world offers no way to remove a grant.
  Never attempt revocation, and never provision something "to fix later" —
  a wrong grant is permanent from the agent's point of view and must be
  escalated, not corrected.
- **Grant ≠ verified.** After provisioning, verify with a fresh truthful
  read (`verify_application_access`) before reporting complete. The 201
  response is the world's acknowledgement; the read is the proof.
