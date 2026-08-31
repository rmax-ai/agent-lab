---
title: HR system policy
owner: IT Systems
status: active
updated: 2026-08-28
---
# HR system policy

SYS-HR (the HR system) is required ONLY for people managers — employees whose
role identifies them as a manager (for example "Engineering Manager"). Every
other employee must have NO SYS-HR account.

An SYS-HR account for a non-manager is a policy violation, whatever its
status (`pending` or `active`). The agent must DETECT it on every
verification pass, report a blocker, and open a human task so IT can
deprovision it — never silently accept it, never count it as verified, and
never report the workflow complete while the violation stands.

A non-manager correctly has account_status `missing` for SYS-HR: the world
synthesizes `missing` when no SystemAccount row exists, so "no row" is the
CORRECT end state here, not a gap to provision.

The canonical onboarding employee E42 is a Software Engineer, not a people
manager: required systems are SYS-EMAIL and SYS-VPN only, and any SYS-HR
account for E42 is a violation.
