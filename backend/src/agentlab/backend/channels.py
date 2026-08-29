"""Channel directory and history service (SPEC §13, PATTERNS §3).

Channels are named topics with natural-language messages on top. The fixed
public set is declared once here; private ``agent:<id>`` channels are derived
elsewhere. Message persistence lives in the ``channel_messages`` table owned by
:mod:`agentlab.backend.models`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from agentlab.backend.models import ChannelMessageRow

# The fixed public channels every agent can use (SPEC §13).
CHANNELS: tuple[str, ...] = (
    "#onboarding",
    "#access",
    "#devices",
    "#systems",
    "#applications",
)

# Prefix for per-agent private channels (``agent:<id>``).
PRIVATE_PREFIX = "agent:"


def list_channels() -> list[str]:
    """Return the fixed public channel ids."""
    return list(CHANNELS)


def get_history(
    session: Session,
    channel: str,
    since: datetime | None,
) -> list[dict[str, Any]]:
    """Return persisted messages for ``channel``, optionally from ``since``.

    Rows are ordered by timestamp then id so the tail is stable and suitable for
    replay by subscribers joining after the fact.
    """
    stmt = select(ChannelMessageRow).where(ChannelMessageRow.channel == channel)
    if since is not None:
        stmt = stmt.where(ChannelMessageRow.ts >= since)
    stmt = stmt.order_by("ts", "id")
    rows = session.exec(stmt).all()
    return [
        {
            "id": row.id,
            "ts": row.ts.isoformat(),
            "channel": row.channel,
            "agent_id": row.agent_id,
            "message": row.message,
        }
        for row in rows
    ]
