"""DEC-08 runtime bounds (SPEC §24).

Central constants so retry, delegation, tool timeout, and HITL SLA bounds live
in one place and can be scenario-overridden later. Stories A.11-A.12 (scenario
engine, domain tools, escalation) import these instead of hard-coding numbers.
"""

from __future__ import annotations

MAX_RETRIES = 3
MAX_DELEGATION_DEPTH = 2
TOOL_TIMEOUT_SECONDS = 30
HITL_NO_RESPONSE_SECONDS = 300
