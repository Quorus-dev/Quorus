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


_ASKS = re.compile(r"\?|\b(approve|approval|confirm|decide|decision|blocked|need you|"
                   r"your call|which one|permission)\b", re.IGNORECASE)


def asks_owner(text: str, owner: str | None) -> str | None:
    """The line where an agent asks *owner* something, or None. A greeting
    that starts "@arav here" is not a question (live 2026-10-17: a roll-call
    reply fired a "needs you" banner)."""
    if not owner:
        return None
    mention = re.compile(rf"(?<![\w-])@{re.escape(owner)}(?![\w-])")
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if mention.search(line):
            window = " ".join(lines[i:i + 2])  # the ask can wrap onto the next line
            if _ASKS.search(window):
                return line
    return None


_DONE = re.compile(r"\b(done|complete|completed|finished|shipped|on main|merged|all set)\b",
                   re.IGNORECASE)


def tells_owner_done(text: str, owner: str | None) -> str | None:
    """The line where an agent tells *owner* the work is finished, or None."""
    if not owner:
        return None
    mention = re.compile(rf"(?<![\w-])@{re.escape(owner)}(?![\w-])")
    for line in (ln.strip() for ln in (text or "").splitlines()):
        if mention.search(line) and _DONE.search(line):
            return line
    return None


def owner_alert(*, agent: str, owner: str | None, reply: str) -> tuple[str, str] | None:
    """``(title, body)`` for the owner's banner, or None to stay quiet."""
    reply = (reply or "").strip()
    if not reply:
        return None
    if reply.startswith(_STUCK_PREFIXES):
        return f"{agent} is stuck", reply
    ask = asks_owner(reply, owner)
    if ask:
        return f"{agent} needs you", ask
    done = tells_owner_done(reply, owner)
    if done:  # a lead's "@arav, both jobs are on main" (live 2026-10-17)
        return f"{agent}: done", done
    if any(line.startswith("(quorus) " + PUBLISHED_MARK) for line in reply.splitlines()):
        return f"{agent} shipped to main", _first_line(reply)
    return None
