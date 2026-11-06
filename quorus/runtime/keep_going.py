"""An agent keeps going on its own until the task is done (Arav, 2026-11-05).

A wake ended when the harness's turn did, so an agent that stopped mid-task
("plan: ..." and then nothing) waited until someone posted again. Now a turn
that isn't finished ends its reply with a line ``/continue`` and the daemon
wakes the agent again for the same request, in the same session.

It stops when a turn ends without ``/continue`` (it posted ✅ or asked with ❓),
after :func:`max_turns` turns in a row, or when a person posts ``/stop`` in the
room. Only a trusted turn may ask, so an untrusted sender can't start a loop,
and each extra turn keeps the trust of the request it continues.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from typing import Any

DEFAULT_MAX_TURNS = 10
GAP_S = 2.0  # before the next turn, so a /stop or a new message can land first

_CONTINUE_LINE = re.compile(r"(?mi)^[ \t]*/continue[ \t.]*$")
# Some models write it at the end of a sentence ("one left, so /continue."):
# opencode on a free model did, and got no next turn (live run, 2026-11-05).
_CONTINUE_LAST = re.compile(r"(?i)[ \t]*(?:,?[ \t]*so[ \t]+)?/continue[ \t.!]*\Z")
_STOP = re.compile(r"(?i)(?:^|\s)/stop\b")
_MENTION = re.compile(r"@[\w.-]+")


def max_turns() -> int:
    """Extra turns in a row an agent may give itself (REFLEXD_MAX_CONTINUES)."""
    try:
        return max(0, int(os.environ.get("REFLEXD_MAX_CONTINUES", DEFAULT_MAX_TURNS)))
    except ValueError:
        return DEFAULT_MAX_TURNS


def asks_to_continue(reply: str) -> bool:
    text = (reply or "").rstrip()
    return bool(_CONTINUE_LINE.search(text) or _CONTINUE_LAST.search(text))


def strip_marker(reply: str) -> str:
    """The reply as the room should see it: without the ``/continue`` line."""
    text = _CONTINUE_LINE.sub("", reply or "").rstrip()
    return _CONTINUE_LAST.sub(".", text).rstrip(" ,") if _CONTINUE_LAST.search(text) else text


def turn_of(envelope: dict[str, Any]) -> int:
    """How many extra turns this request has had so far (0 for the first wake)."""
    try:
        return max(0, int(envelope.get("continue_turn") or 0))
    except (TypeError, ValueError):
        return 0


def next_envelope(envelope: dict[str, Any], since: str) -> dict[str, Any]:
    """The same request again, one turn further. Same ids, so trust, session and
    worktree are the original's. A ``/stop`` posted after the request counts: the
    run is timed from the request, else from *since* (when the run began). Timed
    from the run, a /stop posted 40 s before opencode's run began missed it."""
    return {**envelope, "continue_turn": turn_of(envelope) + 1,
            "continue_since": (envelope.get("continue_since") or envelope.get("timestamp")
                               or since)}


def is_stop_command(content: str) -> bool:
    """A message that is only ``/stop`` (maybe with @-mentions): the daemon acts
    on it and wakes nobody. Woken by it, every agent spent a turn saying
    "Stopped.", and that clean finish queued the half-done work for review."""
    return _MENTION.sub("", content or "").strip().lower().rstrip(".!") == "/stop"


def instructions(turns_left: int) -> str:
    """The wake-intent line that offers ``/continue`` (empty when none are left)."""
    if turns_left <= 0:
        return ""
    return ("- If you stop before the work is done (more steps left and you're not "
            "waiting on anyone), end your reply with a line that says only `/continue`: "
            f"Quorus wakes you again to carry on, up to {turns_left} more turn(s). When "
            "it's done, post ✅. If you need a person, ask with ❓ and don't add "
            "`/continue`.\n")


def resumed_note(turn: int, limit: int) -> str:
    return (f"- Quorus woke you again because your last turn ended with `/continue` "
            f"(turn {turn} of {limit} on your own). Carry on with the triggering request "
            "from where you left off: check the room and your workspace for what's done, "
            "and don't redo it.\n")


def stopped_by(messages: Iterable[dict[str, Any]], *, since: Callable[[Any], bool],
               is_agent: Callable[[str], bool]) -> str | None:
    """The person who posted ``/stop`` in the room during this run, if anyone."""
    for m in messages:
        sender = m.get("from_name") or ""
        if (sender and not is_agent(sender) and since(m.get("timestamp"))
                and _STOP.search(m.get("content") or "")):
            return sender
    return None
