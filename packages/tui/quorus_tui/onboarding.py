"""Room onboarding without model probes, synthetic messages or persisted success."""
from __future__ import annotations

from .chat_widgets import is_human_sender


def room_status(members: list[str], messages: list[dict], *, connected: bool) -> str:
    """Membership is not proof of a response; notices never complete this step.

    Use the same agent-name convention as the chat surface. The status describes
    the loaded history, not a guarantee that an agent process is running.
    """
    if not connected:
        return "Room disconnected — reconnect to check agent status."
    agents = {name for name in members if not is_human_sender(name)}
    if not agents:
        return "Room available · No agents connected · /agents to set up; /share for teammates"
    for msg in reversed(messages):
        kind = msg.get("message_type", "chat")
        body = (msg.get("content") or "").strip()
        if not body:
            continue
        sender = msg.get("from_name") or ""
        if sender not in agents:
            if is_human_sender(sender) and kind == "chat":
                break  # a new human message has not received an answer yet
            continue
        if (body.startswith(("⚠", "❌", "🔐", "[reflexd]", "(quorus)"))
                or body.lower().startswith(("plan:", "no_reply"))
                or "is out of quota" in body.lower()):
            break
        if kind != "chat":
            continue
        return "Agent answered · /agents for setup · /share for teammates"
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
    except Exception:
        console.print("Agent setup failed. Try `quorus doctor` for setup checks.")
        state.set_status_bar("Setup failed. /agents to retry; the room remains available.")
    return True
