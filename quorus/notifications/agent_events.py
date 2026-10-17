"""When an agent's own wake should ping its owner's desktop.

Before 2026-10-17 the only banner came from the hub, and only while it was
open and idle for 30s, and only for an @-mention or DM. Agents reply to the
human without @-mentioning them, so "it's done" never reached anyone who had
walked away. The daemon knows the outcome, so it says it: work shipped, the
agent needs its owner, or the agent is stuck. Plain chat stays quiet.
"""

from __future__ import annotations

import os
import re

PUBLISHED_MARK = "published "
_STUCK_PREFIXES = ("⚠", "[reflexd] harness", "[reflexd] ")


def enabled() -> bool:
    return os.environ.get("QUORUS_NOTIFY", "1").strip().lower() not in ("0", "false", "no", "off")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("(quorus)"):
            return line
    return text.strip()


def owner_alert(*, agent: str, owner: str | None, reply: str) -> tuple[str, str] | None:
    """``(title, body)`` for the owner's banner, or None to stay quiet."""
    reply = (reply or "").strip()
    if not reply:
        return None
    if reply.startswith(_STUCK_PREFIXES):
        return f"{agent} is stuck", reply
    if owner and re.search(rf"(?<![\w-])@{re.escape(owner)}(?![\w-])", reply):
        return f"{agent} needs you", _first_line(reply)
    if any(line.startswith("(quorus) " + PUBLISHED_MARK) for line in reply.splitlines()):
        return f"{agent} shipped to main", _first_line(reply)
    return None
