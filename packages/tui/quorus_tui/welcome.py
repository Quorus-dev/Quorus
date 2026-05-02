"""Welcome / Home view for the Quorus TUI hub.

Rendered ONLY when no room is selected (``HubState.selected_room_idx == -1``).
The instant the user picks a room (Tab, Enter, /join, number), the chat
renderer in ``hub.py`` takes over and this module is silent.

Layout (top → bottom, one blank line between sections):

  1. Banner — softer two-stop gradient (see ``ui._gradient_text``)
  2. Identity rule — ``@you  ·  workspace  ·  welcome home``
  3. Action menu — columned, right-aligned shortcut hints
  4. Chats — segmented "active" / "idle" lists with relative timestamps
  5. Footer — single dim line, comma-separated keybinds

Vertical rhythm: exactly ONE blank line between sections, ZERO inside.
No I/O, no httpx, no threading. Pure Rich rendering against a snapshot
of HubState — so it is trivially testable and never blocks the loop.
"""

from __future__ import annotations

from typing import Iterable

from quorus_cli.ui import _BANNER_LINES, _gradient_text  # type: ignore[attr-defined]
from rich.console import Console
from rich.text import Text

from .render import (
    INDENT,
    SEP,
    relative_time,
    section_head,
    two_col,
)

# Single-key actions surfaced in the welcome view. Order matters — this is
# the table the user reads top-to-bottom. Each tuple: (key, label, hint).
_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("n", "New room",         "create a room and switch to it"),
    ("j", "Join by invite",   "paste a quorus_join_ token"),
    ("r", "Rooms",            "full numbered list with member counts"),
    ("s", "Share <room>",     "mint an invite token for a room"),
    ("d", "Delete <room>",    "destroy a room (confirmation required)"),
    ("/", "Slash palette",    "type / to see every command"),
    ("?", "Help",             "full keybinds + slash commands"),
    ("q", "Quit",             "leave the hub"),
)

# Footer: dim, comma-separated, single line. Pinned by render order.
_FOOTER_HINT = (
    "Tab — switch room, Enter — open, Esc — back home, / — slash command"
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

    # ── 1. Banner ────────────────────────────────────────────────────────────
    console.print()
    if width >= 60:
        for line in _BANNER_LINES:
            console.print(_gradient_text(line))
    else:
        console.print(Text(f"{INDENT}quorus", style="bold primary"))

    # Tagline directly under the banner — single line, no decoration.
    console.print()
    console.print(Text(
        f"{INDENT}coordination layer for AI agent swarms",
        style="muted italic",
    ))

    # ── 2. Identity rule ─────────────────────────────────────────────────────
    console.print()
    sub = Text(INDENT)
    sub.append(f"@{agent_name}", style="bold agent")
    if workspace_label:
        sub.append(SEP, style="dim")
        sub.append(workspace_label, style="accent")
    sub.append(SEP, style="dim")
    sub.append("welcome home", style="muted")
    console.print(sub)

    # ── 3. Action menu ───────────────────────────────────────────────────────
    console.print()
    console.print(section_head("Actions"))
    for key, label, hint in _ACTIONS:
        # Left half: shortcut + label (constant width so columns align).
        left = Text(INDENT)
        left.append(f"[{key}]", style="kbd")
        left.append("  ")
        left.append(label, style="bright")
        # Right half: dim description, right-aligned within the menu band.
        right = Text(hint, style="muted")
        # Cap menu band at 80 cols so right-edge doesn't drift off-screen
        # on ultra-wide terminals (still flush-right inside the band).
        band = min(width, 78)
        console.print(two_col(left, right, total_width=band))

    # ── 4. Chats panel ───────────────────────────────────────────────────────
    console.print()

    if not rooms:
        # Warm empty state — invitation, not statement of fact.
        msg = Text(INDENT)
        msg.append("No rooms yet. ", style="bright")
        msg.append("Press ", style="muted")
        msg.append("[n]", style="kbd")
        msg.append(" to create your first.", style="muted")
        console.print(msg)
        console.print()
        console.print(_footer_hint_line())
        return

    sorted_rooms = sort_rooms_by_activity(rooms, unread_by_room)
    fresh_rooms = [r for r in sorted_rooms
                   if unread_by_room.get(_room_key(r), 0) > 0]
    idle_rooms  = [r for r in sorted_rooms
                   if unread_by_room.get(_room_key(r), 0) == 0]

    # Section head: count + optional unread accent.
    accent = (
        f"{len(fresh_rooms)} with new activity"
        if fresh_rooms else f"{len(rooms)} total"
    )
    console.print(section_head("Your chats", accent=accent))
    console.print()

    preview_width = max(20, width // 3)
    band = min(width, 78)

    if fresh_rooms:
        for row in _chat_rows(
            fresh_rooms, unread_by_room,
            selected_room_name, messages, preview_width, band, fresh=True,
        ):
            console.print(row)
        if idle_rooms:
            console.print()

    if idle_rooms:
        if fresh_rooms:
            # Subtle divider hint between the two buckets.
            console.print(Text(f"{INDENT}— idle —", style="dim"))
        for row in _chat_rows(
            idle_rooms, unread_by_room,
            selected_room_name, messages, preview_width, band, fresh=False,
        ):
            console.print(row)

    # ── 5. Footer ────────────────────────────────────────────────────────────
    console.print()
    console.print(_footer_hint_line())


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
    band_width: int,
    *,
    fresh: bool,
) -> list[Text]:
    """Build one Text row per room.

    Layout per row (band_width chars wide, two-column composition):

      ``  #name   N members [· preview…]              [unread •N | timestamp]``

    Fresh rooms get a bold left-bar accent on the room name; idle rooms
    fade to muted. Member counts are always subdued. Timestamps render
    as ``"2h ago"`` when ``created_at`` is present; otherwise the right
    column is blank (we never invent signal we don't have).
    """
    rows: list[Text] = []
    for room in rooms:
        name = room.get("name") or room.get("id", "?")
        members = room.get("members") or []
        if isinstance(members, list) and members:
            member_count = len(members)
        else:
            member_count = int(room.get("member_count") or 0)
        unread = unread_by_room.get(name, 0)
        preview = _last_preview_for_room(
            name, selected_room_name, messages, preview_width,
        )
        rel_ts = relative_time(room.get("created_at", ""))

        # Left half: room name + member count + (optional) preview.
        left = Text(INDENT)
        if fresh:
            # Fresh rooms get a bold amber name — they should pop.
            left.append(f"#{name}", style="bold room")
        else:
            # Idle rooms fade to subtle so the eye finds the fresh ones.
            left.append(f"#{name}", style="subtle")
        left.append("   ")
        left.append(
            f"{member_count} {'member' if member_count == 1 else 'members'}",
            style="muted",
        )
        if preview:
            left.append(SEP, style="dim")
            left.append(preview, style="dim")

        # Right half: unread badge OR timestamp. Never both — unread is
        # the higher-signal field, so it wins when present.
        right = Text()
        if unread:
            right.append(f"unread •{unread}", style="bold room")
        elif rel_ts:
            right.append(rel_ts, style="muted")

        rows.append(two_col(left, right, total_width=band_width))
    return rows


def _footer_hint_line() -> Text:
    """Footer hint — dim, single line, comma-separated. Same every render."""
    return Text(f"{INDENT}{_FOOTER_HINT}", style="dim")
