"""Welcome / Home view for the Quorus TUI hub.

Rendered ONLY when no room is selected (``HubState.selected_room_idx == -1``).
The instant the user picks a room (Tab, Enter, /join, number), the chat
renderer in ``hub.py`` takes over and this module is silent.

Layout (top → bottom):
  1. Quorus banner (gradient — reused from quorus_cli.ui)
  2. Action menu — single-key shortcuts wired to existing slash handlers
  3. Past / current chats panel — sorted by recent activity
  4. Footer hint — single line

No I/O, no httpx, no threading. Pure Rich rendering against a snapshot
of HubState — so it is trivially testable and never blocks the loop.
"""

from __future__ import annotations

from typing import Iterable

from quorus_cli.ui import _BANNER_LINES, _gradient_text  # type: ignore[attr-defined]
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

# Single-key actions surfaced in the welcome view. Order matters — this is
# the table the user reads top-to-bottom. Each tuple: (key, label, hint).
_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("n", "New room",         "create a room and switch to it"),
    ("j", "Join by invite",   "paste a quorus_join_ token"),
    ("r", "Rooms",            "full numbered list with member counts"),
    ("s", "Share <room>",     "mint an invite token for a room"),
    ("d", "Delete <room>",    "destroy a room (confirmation required)"),
    ("/", "Slash palette",    "type a / to see every command"),
    ("?", "Help",             "full keybinds + slash commands"),
    ("q", "Quit",             "leave the hub"),
)

_FOOTER_HINT = (
    "Tab — switch room  ·  Enter — open  ·  Esc — back to home  "
    "·  / — slash command"
)


def sort_rooms_by_activity(
    rooms: list[dict],
    unread_by_room: dict[str, int],
) -> list[dict]:
    """Return *rooms* sorted by recent activity (most-active first).

    Activity proxy (in priority order):
      1. Rooms with unread messages — highest unread count first.
      2. Rooms with a parseable ``created_at`` ISO timestamp — newest first.
      3. Everything else, alphabetical.

    The relay does not currently expose ``last_message_ts`` on the
    /rooms list, so unread-count is the strongest live signal we have.
    Once the API grows a real ``last_active_at`` field, swap step 2.
    """
    def _key(room: dict) -> tuple[int, int, str, str]:
        raw_name = room.get("name") or room.get("id") or ""
        name = raw_name.lower()  # only used for tie-breaker alphabetisation
        unread = unread_by_room.get(raw_name, 0)
        # Sort key tiers: presence of unread → unread count → created_at desc → name asc.
        # Negate counts so higher = earlier; created_at is sorted lexicographically
        # (ISO-8601 sorts correctly without parsing).
        created = room.get("created_at") or ""
        # `created` reverse: invert by lexicographic complement is hard, so
        # just bucket: rooms with created_at land before those without.
        has_created = 1 if created else 0
        return (
            -1 if unread > 0 else 0,    # unread bucket first
            -unread,                     # higher unread earlier
            # Reverse-sortable ISO8601 — invert via "~" complement trick.
            # Since "~" sorts after digits, inverting gets newest-first.
            "~" + created if has_created else "z" + name,
            name,
        )

    return sorted(rooms, key=_key)


def _last_preview_for_room(
    room_name: str,
    selected_room_name: str,
    messages: list[dict],
    width: int,
) -> str:
    """Return a one-line preview of the most recent message in *room_name*.

    HubState only caches messages for the currently-selected room — so we
    can only produce a real preview for that room. For every other room
    we return an empty string and let the caller render a muted placeholder.
    """
    if room_name != selected_room_name or not messages:
        return ""
    last = messages[-1]
    sender = last.get("from_name") or last.get("sender", "?")
    body = (last.get("content") or "").replace("\n", " ").strip()
    raw = f"@{sender}: {body}"
    return raw if len(raw) <= width else raw[: width - 1] + "…"


def render_welcome(
    console: Console,
    *,
    rooms: list[dict],
    unread_by_room: dict[str, int],
    selected_room_name: str,
    messages: list[dict],
    agent_name: str,
    workspace_label: str = "",
) -> None:
    """Render the full welcome view to *console*.

    Pure side-effect (Rich console.print) — no return value. Caller is
    expected to have already cleared the screen and printed the header.
    """
    width = max(40, console.size.width)

    # ── Banner ───────────────────────────────────────────────────────────────
    if width >= 60:
        console.print()
        for line in _BANNER_LINES:
            console.print(_gradient_text(line))
        console.print()
    else:
        console.print()
        console.print(Text("  quorus", style="bold primary"))
        console.print()

    # Subtle context line: who you are, where you're connected.
    sub = Text("  ")
    sub.append(f"@{agent_name}", style="bold agent")
    if workspace_label:
        sub.append("  ·  ", style="dim")
        sub.append(workspace_label, style="accent")
    sub.append("  ·  ", style="dim")
    sub.append("welcome home", style="muted italic")
    console.print(sub)
    console.print()

    # ── Action menu ──────────────────────────────────────────────────────────
    console.print(Text.from_markup("  [bold primary]Actions[/]"))
    for key, label, hint in _ACTIONS:
        line = Text("  ")
        line.append(f"[{key}]", style="bold accent")
        line.append("  ")
        line.append(f"{label:<18}", style="bright_white")
        line.append(hint, style="dim")
        console.print(line)
    console.print()

    # ── Past / current chats ─────────────────────────────────────────────────
    console.print(Rule(style="dim"))
    console.print()
    if not rooms:
        console.print(Text.from_markup(
            "  [bold primary]Your chats[/]  "
            "[dim]· no rooms yet — press [bold]n[/] to create one[/]"
        ))
        console.print()
        console.print(_FOOTER_HINT_LINE())
        return

    sorted_rooms = sort_rooms_by_activity(rooms, unread_by_room)
    fresh_rooms = [r for r in sorted_rooms
                   if unread_by_room.get(_room_key(r), 0) > 0]
    idle_rooms  = [r for r in sorted_rooms
                   if unread_by_room.get(_room_key(r), 0) == 0]

    header = Text("  ")
    header.append("Your chats", style="bold primary")
    header.append("  ·  ", style="dim")
    header.append(f"{len(rooms)} total", style="muted")
    if fresh_rooms:
        header.append("  ·  ", style="dim")
        header.append(f"{len(fresh_rooms)} with new activity", style="bold room")
    console.print(header)
    console.print()

    preview_width = max(20, width // 3)

    if fresh_rooms:
        console.print(Text.from_markup("  [dim]── new activity ──[/]"))
        for row in _chat_rows(
            fresh_rooms, unread_by_room,
            selected_room_name, messages, preview_width,
        ):
            console.print(row)
        console.print()

    if idle_rooms:
        if fresh_rooms:
            console.print(Text.from_markup("  [dim]── idle ──[/]"))
        for row in _chat_rows(
            idle_rooms, unread_by_room,
            selected_room_name, messages, preview_width,
        ):
            console.print(row)
        console.print()

    console.print(_FOOTER_HINT_LINE())


def _room_key(room: dict) -> str:
    """Return the case-preserved name used as the unread-dict key.

    HubState stores unread counts under the room's verbatim name (no
    case-folding), so this MUST mirror that. Lower-casing here would
    silently zero every badge.
    """
    return room.get("name") or room.get("id") or ""


def _chat_rows(
    rooms: Iterable[dict],
    unread_by_room: dict[str, int],
    selected_room_name: str,
    messages: list[dict],
    preview_width: int,
) -> list[Text]:
    """Build one Text row per room. Each row:

      ``  #name  ·  N members  ·  preview…  ·  unread•N``
    """
    rows: list[Text] = []
    for room in rooms:
        name = room.get("name") or room.get("id", "?")
        # Relay returns either a member list (`members`) or a precomputed count
        # (`member_count`). Honour whichever is present.
        members = room.get("members") or []
        if isinstance(members, list) and members:
            member_count = len(members)
        else:
            member_count = int(room.get("member_count") or 0)
        unread = unread_by_room.get(name, 0)
        preview = _last_preview_for_room(
            name, selected_room_name, messages, preview_width,
        )

        line = Text("  ")
        # Room name in amber, bold if it has unread.
        line.append(f"#{name}", style="bold room" if unread else "room")
        line.append("  ·  ", style="dim")
        line.append(
            f"{member_count} {'member' if member_count == 1 else 'members'}",
            style="muted",
        )
        if preview:
            line.append("  ·  ", style="dim")
            line.append(preview, style="dim")
        if unread:
            line.append("  ·  ", style="dim")
            line.append(f"unread •{unread}", style="bold room")
        rows.append(line)
    return rows


def _FOOTER_HINT_LINE() -> Text:
    """Build the welcome footer hint — same on every render."""
    return Text(f"  {_FOOTER_HINT}", style="dim")
