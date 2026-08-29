"""Agent Lab Onboarding coordinator agent (SPEC §11, story A.10).

The coordinator owns ``employee_onboarding_ready``. It delegates business
outcomes to the domain agents and never touches domain state itself.
"""

from agentlab.onboarding.coordinator import (
    DEFAULT_GOAL,
    DOMAIN_GOALS,
    POLL_INTERVAL_SECONDS,
    REQUIRED_GOALS_BY_PROFILE,
    CoordinatorAgent,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_GOAL",
    "DOMAIN_GOALS",
    "POLL_INTERVAL_SECONDS",
    "REQUIRED_GOALS_BY_PROFILE",
    "CoordinatorAgent",
]
