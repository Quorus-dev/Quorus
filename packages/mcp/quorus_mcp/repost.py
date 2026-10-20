"""Don't post the same thing twice from one retried turn.

A wake can run its harness more than once: a fresh retry after a failed resume,
or the next free opencode model after a rate limit. Each run gets a new MCP
server, so a plan line the first run already posted came again, reworded or not
(bug bash, `quorus demo` on 0.6.2: "plan: inspect commit 5c3bfb7…" at 73 s and
83 s). The room's own history is the only memory every run shares.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

WINDOW_S = 600  # a retried turn reposts within minutes, not hours
_PLAN = re.compile(r"\s*plan\s*:", re.IGNORECASE)


def _age_s(msg: dict[str, Any], now: datetime) -> float | None:
    try:
        ts = datetime.fromisoformat(str(msg.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds()


def duplicate_of(history: list[dict[str, Any]], sender: str, content: str,
                 now: datetime | None = None) -> dict[str, Any] | None:
    """The message in *history* (any order) that *content* from *sender*
    would repeat, or None. Repeats: the same text from the same sender within
    WINDOW_S, or a plan line when the sender's latest message is a plan line
    from within WINDOW_S (a turn that planned, never answered, and ran again)."""
    now = now or datetime.now(timezone.utc)
    text = content.strip()
    mine = sorted((m for m in history if (m.get("from_name") or "") == sender),
                  key=lambda m: str(m.get("timestamp") or ""))
    for m in reversed(mine):
        age = _age_s(m, now)
        if age is None or age > WINDOW_S:
            break
        if (m.get("content") or "").strip() == text:
            return m
    if _PLAN.match(text) and mine:
        last = mine[-1]
        age = _age_s(last, now)
        if age is not None and age <= WINDOW_S and _PLAN.match(last.get("content") or ""):
            return last
    return None
