"""Slash-command additions for the chat surface.

Lives outside ``hub.py`` so the file doesn't keep growing. The legacy
slash table in ``hub.SLASH_COMMANDS`` is preserved (handlers there stay
put — moving them all would create churn for no benefit). This module
adds the new WhatsApp/Instagram-style verbs and the shared share-card
flow that both the `s` shortcut and ``/share`` use.

Public surface
--------------
* :func:`run_share_flow`  — mint + display the share card, handle `c` to copy
* :func:`slash_leave`     — `/leave` self-leave (state-only — no destroy)
* :func:`slash_info`      — `/info` alias for the room-state pane
* :func:`slash_me`        — `/me <action>` IRC-style action message
* :func:`slash_mute`      — `/mute <duration>` placeholder UI

Each handler uses the same `(arg, state, relay_url, secret, agent_name,
console)` signature as the existing `hub._slash_*` functions.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from . import chat as _chat

# ── Share flow (used by both `s` and `/share`) ───────────────────────────────


def run_share_flow(
    *,
    console: Console,
    room_name: str,
    code: str,
    install_url: str | None = None,
    read_one_key=None,
) -> str:
    """Render the share card and block until the user dismisses it.

    Parameters
    ----------
    console        rich Console to print into
    room_name      room being shared (display only)
    code           join code returned by ``_mint_join_token``
    install_url    optional one-line install URL for the card
    read_one_key   optional callable returning one keypress char. Defaults
                   to ``readchar.readchar`` when available, else stdin
                   blocking ``input()``. Tests pass a stub.

    Returns
    -------
    Status string suitable for ``state.set_status_bar``:
        "Copied invite for #room"  (after `c`)
        "Share card closed"        (any other key)
    """
    width = max(_chat.SHARE_CARD_MIN_W + 4, console.size.width if console.size else 80)
    console.print()
    for line in _chat.render_share_card(
        room_name=room_name,
        code=code,
        install_url=install_url,
        console_width=width,
    ):
        console.print(line)
    console.print()

    if read_one_key is None:
        read_one_key = _default_one_key

    try:
        key = read_one_key()
    except (KeyboardInterrupt, EOFError):
        return "Share card closed"

    if key and key.lower() == "c":
        if _chat.copy_to_clipboard(code):
            return f"Copied invite for #{room_name} to clipboard"
        return f"Couldn't copy — code: {code}"
    return f"Share card closed for #{room_name}"


def _default_one_key() -> str:
    """One-keypress reader. Falls back to blocking input() w/o readchar."""
    try:
        import readchar  # type: ignore

        return readchar.readkey()
    except ImportError:
        try:
            line = input()
            return (line[:1] if line else "")
        except EOFError:
            return ""


# ── /leave — leave current room (state-only — no destroy) ────────────────────


def slash_leave(arg, state, relay_url, secret, agent_name, console):
    """`/leave` — return to the welcome view from the current room.

    Distinct from `/delete` (owner-only destroy). Pure state mutation —
    we do not call any relay endpoint because Quorus has no leave-room
    semantics yet (membership is implicit from message authorship).
    Renaming is a UX choice: most users mean "exit this room" when they
    type leave; `/delete` retains the destructive verb.
    """
    del arg, relay_url, secret, agent_name
    name = state.selected_room_name()
    with state._lock:
        state.selected_room_idx = -1
    state.set_messages([])
    if name:
        state.set_status_bar(f"Left #{name}")
    else:
        state.set_status_bar("")
    console.print()
    return True


# ── /info — alias for the room-state pane ────────────────────────────────────


def slash_info(arg, state, relay_url, secret, agent_name, console):
    """`/info` — print members, room name, and last-active time.

    Lightweight alias around what the welcome view shows for selected
    rooms. Doesn't hit any new endpoint — uses the snapshot the poll
    loop already maintains.
    """
    del arg, relay_url, secret, agent_name
    selected = state.get_selected_room()
    if not selected:
        state.set_status_bar("No room selected — try /join <room> first.")
        return True
    name = selected.get("name") or selected.get("id", "?")
    members = selected.get("members") or []
    msgs = state.get_messages()
    last = _chat.last_active_label(msgs)

    console.print()
    console.print(Text.from_markup(f"  [bold primary]#{name}[/]"))
    if last:
        console.print(Text.from_markup(f"  [muted]{last}[/]"))
    console.print()
    console.print(Text.from_markup(
        f"  [bold primary]Members[/]  [dim]{len(members)} total[/]"
    ))
    if members:
        for m in members:
            line = Text("  ●  ", style=_chat._sender_color(m))
            line.append(f"@{m}", style=f"bold {_chat._sender_color(m)}")
            console.print(line)
    else:
        console.print(Text.from_markup("  [dim]no member roster yet[/]"))
    console.print()
    return True


# ── /me — IRC-style action message ───────────────────────────────────────────


def slash_me(arg, state, relay_url, secret, agent_name, console):
    """`/me <action>` — sends an action-styled chat message.

    Implementation note: the relay's message schema doesn't have a
    dedicated 'action' type, so we send as a regular chat message with
    an asterisk-prefixed body. Recipients render it normally; the visual
    distinction lives client-side in render conventions.
    """
    del console
    action = (arg or "").strip()
    if not action:
        state.set_status_bar("usage: /me <action>  (e.g. /me is debugging)")
        return True
    selected = state.get_selected_room()
    if not selected:
        state.set_status_bar("No room selected — /join <room> first.")
        return True
    room_name = selected.get("name") or selected.get("id") or ""
    if not room_name:
        state.set_status_bar("Couldn't determine room name.")
        return True

    # Lazy import to avoid a circular dep with hub.py.
    # Use the active chat_identity (human override of agent_name) so /me
    # actions tag the human user, not their agent. Falls back to agent_name
    # when chat_identity isn't set (legacy profiles, fresh init).
    from quorus.config import ConfigManager as _CM

    from .hub import _send_message  # type: ignore
    _profile = _CM().load() or {}
    sender = _profile.get("chat_identity") or agent_name
    body = f"* {sender} {action}"
    sent_id = _send_message(relay_url, secret, room_name, sender, body)
    if sent_id is not None:
        state.set_status_bar("")
        from datetime import datetime, timezone
        echo = {
            "from_name": sender,
            "content": body,
            "message_type": "chat",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "room": room_name,
        }
        if sent_id:
            echo["id"] = sent_id
            echo["message_id"] = sent_id
        state.append_message(echo)
    else:
        state.set_status_bar("Couldn't reach the relay — message not sent.")
    return True


# ── /mute — forward-looking placeholder UI ───────────────────────────────────


def slash_mute(arg, state, relay_url, secret, agent_name, console):
    """`/mute <duration>` — placeholder UI for room muting.

    No backend mute endpoint exists yet. We surface the UI so the muscle
    memory builds correctly; the status bar carries a clear "(coming
    soon)" badge so users aren't misled.
    """
    del relay_url, secret, agent_name, console
    duration = (arg or "").strip() or "1h"
    selected = state.get_selected_room()
    if not selected:
        state.set_status_bar("No room selected.")
        return True
    name = selected.get("name") or "?"
    state.set_status_bar(f"#{name} muted for {duration} (coming soon)")
    return True


__all__ = [
    "run_share_flow",
    "slash_info",
    "slash_leave",
    "slash_me",
    "slash_mute",
]
