"""Room onboarding without model probes, synthetic messages or persisted success."""
from __future__ import annotations

from .chat_widgets import is_human_sender

# The daemon's notice when opencode's free tier refused (reflexd.free_tier_notice):
# the agent is up but no free model can answer, which is neither "waiting" nor "answered".
FREE_TIER_MARK = "free tier has hit its limit"


def room_status(members: list[str], messages: list[dict], *, connected: bool) -> str | None:
    """Membership is not proof of a response; notices never complete this step.

    Use the same agent-name convention as the chat surface. The status describes
    the loaded history, not a guarantee that an agent process is running.
    """
    free_tier_limited = False
    for msg in messages:
        body = (msg.get("content") or "").strip()
        sender = msg.get("from_name") or ""
        if (not body or not sender or is_human_sender(sender)
                or msg.get("message_type", "chat") != "chat"):
            continue
        if (body.startswith(("⚠", "❌", "🔐", "[reflexd]", "(quorus)"))
                or body.lower().startswith(("plan:", "no_reply"))
                or "is out of quota" in body.lower()):
            free_tier_limited = free_tier_limited or FREE_TIER_MARK in body
            continue
        # Onboarding is complete even if a later human message is unanswered,
        # or the agent that answered has since left the room.
        return None
    if not connected:
        return "Room disconnected — reconnect to check agent status."
    if not any(not is_human_sender(name) for name in members):
        return "Room available · No agents connected · /agents to set up; /share for teammates"
    if free_tier_limited:
        return ("No free model answered yet · the agent retries after its pause · "
                "/agents for other options")
    return "Agent joined; waiting for a response · /agents to retry setup"


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
