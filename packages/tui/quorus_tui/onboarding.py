"""Room onboarding without model probes, synthetic messages or persisted success."""
from __future__ import annotations

from typing import Any

from .chat_widgets import is_human_sender, ts_epoch

# The daemon's notice when opencode's free tier refused (reflexd.free_tier_notice):
# the agent is up but no free model can answer, which is neither "waiting" nor "answered".
FREE_TIER_MARK = "free tier has hit its limit"
FREE_TIER_TEXT = ("No free model answered yet · message the agent again after its pause · "
                  "/agents for other options")
WAIT_HINT_S = 120


def _agent_line(msg: dict) -> tuple[str, str] | None:
    """``(sender, body)`` for an agent's chat line; None for humans and other types."""
    body = (msg.get("content") or "").strip()
    sender = msg.get("from_name") or ""
    if (not body or not sender or is_human_sender(sender)
            or msg.get("message_type", "chat") != "chat"):
        return None
    return sender, body


def _is_notice(body: str) -> bool:
    """Warnings, status lines, plans and quota lines are never an answer."""
    return (body.startswith(("⚠", "❌", "🔐", "[reflexd]", "(quorus)"))
            or body.lower().startswith(("plan:", "no_reply"))
            or "is out of quota" in body.lower())


def _names(names: list[str]) -> str:
    return " · ".join(f"@{n}" for n in names)


def _elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"


def room_state(
    connected: bool, members: list[str], presence: list[dict[str, Any]] | None,
    messages: list[dict], now: float, *, room: str,
    busy_since: dict[str, float] | None = None,
) -> tuple[str, str]:
    """``(state, text)`` for the room's status row (tui-polish.md §3.3).

    Rows are checked in order and the first match wins. Readiness comes only
    from the relay's own ``online`` flag in ``/presence``: an agent member with
    no fresh row (or no presence at all) is not running, never ready. Notices,
    status posts, plans and ``NO_REPLY`` are never an answer.
    """
    if not connected:
        return ("disconnected",
                "Not connected to the relay · retrying · /status for details")
    agents = [m for m in members if not is_human_sender(m)]
    if not agents:
        return ("no agents",
                "No agents here yet · /agents to add one · /share to invite a person")
    rows = {p.get("name"): p for p in presence or [] if p.get("online")}
    online = [a for a in agents if a in rows]
    if not online:
        return ("not running", f"{_names(agents)} isn't running · /agents to restart it")
    busy = [a for a in online
            if rows[a].get("status") == "busy" and rows[a].get("room") == room]
    if busy:
        since = busy_since or {}
        parts = [f"@{a} {rows[a].get('activity') or 'working'} "
                 f"{_elapsed(now - since.get(a, now))}" for a in busy]
        return "working", "⋯ " + " · ".join(parts)
    # Positions in history order, not timestamps: a missing or tied timestamp
    # must not reorder a question and its answer.
    human = answer = free_busy = -1
    human_ts = 0.0
    for i, msg in enumerate(messages):
        line = _agent_line(msg)
        if line is None:
            sender = msg.get("from_name") or ""
            if (sender and is_human_sender(sender) and (msg.get("content") or "").strip()
                    and msg.get("message_type", "chat") == "chat"):
                human, human_ts = i, ts_epoch(msg.get("timestamp", ""))
            continue
        if not _is_notice(line[1]):
            answer = i
        elif FREE_TIER_MARK in line[1]:
            free_busy = i
    if free_busy > max(human, answer):
        return "free tier limited", FREE_TIER_TEXT
    if human < 0:
        return "ready", f"{_names(online)} is here · say something to wake it"
    if human > answer:
        waited = now - human_ts if human_ts else 0.0
        text = f"{_names(online)} hasn't picked this up yet · {_elapsed(waited)}"
        return "waiting", text + (" · /agents to check it" if waited >= WAIT_HINT_S else "")
    return "answered", "@ mention · / commands · ^D quit"


def setup_agents(arg, state, relay_url, secret, agent_name, console):
    """An explicit, repeatable setup action; cancellation leaves the room intact."""
    from quorus_cli.quickstart import offer

    from quorus.config import load_config

    from . import hub

    room = state.get_selected_room()
    if not room:
        state.set_status_bar("Open a room first, then /agents to set up an agent.")
        return True
    if arg.strip():
        state.set_status_bar("Use /agents without arguments to choose installed agents.")
        return True
    try:
        invitee = hub._invited_elsewhere(load_config().get("api_key") or "", relay_url)
        added = offer(console, room["name"], invitee=invitee)
        state.set_status_bar("Agent setup finished; waiting for a real response." if added else
                             "Room available without an agent. /agents to try setup later.")
    except (KeyboardInterrupt, EOFError):
        console.print()
        state.set_status_bar("Setup canceled. Your room is still available; /agents to retry.")
    except Exception as exc:
        console.print(f"Agent setup failed: {exc}. Try `quorus doctor` for setup checks.",
                      markup=False)
        state.set_status_bar("Setup failed. /agents to retry; the room remains available.")
    return True
