#!/usr/bin/env python3
"""
murmur_tui.py — Rich terminal UI for Murmur relay chat.

Usage:
  python3 murmur_tui.py [--name NAME] [--room ROOM]

Keyboard shortcuts:
  Enter  — send message
  Ctrl+C — quit
"""
import argparse
import sys
import threading
import time
from datetime import datetime, timezone

import httpx

try:
    from rich import box
    from rich.columns import Columns
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("rich not installed — pip install rich")
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────────────────────
RELAY   = "http://100.127.251.47:9000"
SECRET  = "medport-secret"
ROOM    = "test-room"
POLL_S  = 2
MAX_MSG = 30   # how many messages to show in the panel

# ── Shared state ─────────────────────────────────────────────────────────────
_messages: list[dict] = []
_agents:   list[dict] = []
_seen_ids: set[str]   = set()
_lock = threading.Lock()
_console = Console()

HEADERS = {"Authorization": f"Bearer {SECRET}"}

ACCENT_COLORS = {
    "arav":          "bright_cyan",
    "aarya":         "bright_magenta",
    "website-agent": "bright_yellow",
    "claude":        "bright_blue",
    "Aravs-baby-boy":"bright_green",
}

def _color(sender: str) -> str:
    return ACCENT_COLORS.get(sender, "white")

def _fetch_history(room: str) -> list[dict]:
    try:
        r = httpx.get(
            f"{RELAY}/rooms/{room}/history",
            headers=HEADERS,
            params={"limit": MAX_MSG},
            timeout=5,
        )
        data = r.json()
        return data if isinstance(data, list) else data.get("messages", [])
    except Exception:
        return []

def _fetch_agents() -> list[dict]:
    try:
        r = httpx.get(f"{RELAY}/agents", headers=HEADERS, timeout=5)
        data = r.json()
        if isinstance(data, list):
            return data
        return data.get("agents", [])
    except Exception:
        return []

def _fetch_rooms() -> list[dict]:
    try:
        r = httpx.get(f"{RELAY}/rooms", headers=HEADERS, timeout=5)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

def _send(room: str, name: str, content: str) -> bool:
    try:
        r = httpx.post(
            f"{RELAY}/rooms/{room}/messages",
            headers=HEADERS,
            json={"from_name": name, "content": content, "message_type": "chat"},
            timeout=5,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False

# ── Background poller ─────────────────────────────────────────────────────────
def _poll(room: str) -> None:
    global _messages, _agents
    while True:
        msgs = _fetch_history(room)
        agents = _fetch_agents()
        with _lock:
            _messages = msgs[-MAX_MSG:]
            _agents   = agents
        time.sleep(POLL_S)

# ── Renderers ─────────────────────────────────────────────────────────────────
def _render_agents() -> Panel:
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    table.add_column("Agent", style="bold", min_width=14)
    table.add_column("Status", min_width=8)

    with _lock:
        agents = list(_agents)

    if not agents:
        # Fallback: show known members from room
        known = ["arav", "aarya", "website-agent", "claude"]
        for name in known:
            table.add_row(
                Text(name, style=_color(name)),
                Text("?", style="dim"),
            )
    else:
        for a in agents:
            name   = a.get("name", "?")
            status = a.get("status", "?")
            color  = "green" if status == "active" else "dim"
            dot    = "● " if status == "active" else "○ "
            table.add_row(
                Text(name, style=_color(name)),
                Text(dot + status, style=color),
            )

    return Panel(table, title="[bold]Agents[/bold]", border_style="dim", padding=(0, 1))

def _render_chat(room: str, my_name: str) -> Panel:
    with _lock:
        msgs = list(_messages)

    lines = Text()
    for m in msgs:
        sender  = m.get("from_name") or m.get("sender", "?")
        content = m.get("content", "")
        ts_raw  = m.get("timestamp", "")
        mtype   = m.get("message_type", "chat")
        try:
            ts = ts_raw[:16].replace("T", " ")
        except Exception:
            ts = "??:??"

        # Timestamp
        lines.append(f"[{ts}] ", style="dim")

        # Sender
        color = _color(sender)
        is_me = sender == my_name
        style = f"bold {color}" + (" on dark_blue" if is_me else "")
        lines.append(sender, style=style)

        # Message type tag
        if mtype not in ("chat", ""):
            lines.append(f" [{mtype}]", style="dim italic")

        lines.append(": ")
        lines.append(content + "\n", style="white" if not is_me else "bright_white")

    return Panel(
        lines,
        title=f"[bold]#{room}[/bold]",
        border_style="bright_cyan",
        padding=(0, 1),
    )

def _render_room_list(rooms: list[dict]) -> Panel:
    t = Text()
    for r in rooms:
        name    = r.get("name", "?")
        members = r.get("members", [])
        t.append(f"  #{name}", style="bold bright_cyan")
        t.append(f"  ({len(members)} members)\n", style="dim")
    return Panel(t, title="[bold]Rooms[/bold]", border_style="dim", padding=(0, 1))

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Murmur terminal chat UI")
    parser.add_argument("--name", default="arav",      help="Your agent name")
    parser.add_argument("--room", default=ROOM,        help="Room to join")
    args = parser.parse_args()

    name = args.name
    room = args.room

    # Join the room
    try:
        httpx.post(
            f"{RELAY}/rooms/{room}/join",
            headers=HEADERS,
            json={"participant": name},
            timeout=5,
        )
    except Exception:
        pass

    # Seed initial data
    with _lock:
        _messages.extend(_fetch_history(room)[-MAX_MSG:])
        _agents.extend(_fetch_agents())

    rooms = _fetch_rooms()

    # Start background poller
    t = threading.Thread(target=_poll, args=(room,), daemon=True)
    t.start()

    _console.print()
    _console.print(
        f"[bold bright_cyan]Murmur TUI[/bold bright_cyan] — "
        f"room [bold]#{room}[/bold] as [bold]{name}[/bold]  "
        "[dim](Ctrl+C to quit)[/dim]"
    )
    _console.print("[dim]─" * 60 + "[/dim]")

    def _snapshot() -> str:
        """Render the whole view as a static string for one-shot printing."""
        from io import StringIO

        from rich.console import Console as C
        buf = StringIO()
        c = C(file=buf, width=120, no_color=False, highlight=False)
        left  = _render_agents()
        right = _render_chat(room, name)
        rl    = _render_room_list(rooms)
        c.print(Columns([left, rl], equal=False, expand=False))
        c.print(right)
        return buf.getvalue()

    # ── Interactive loop (non-Live, so input works) ──────────────────────────
    last_render = 0.0
    input_prompt = f"\n[{name}]> "

    while True:
        now = time.monotonic()
        if now - last_render > POLL_S:
            _console.clear()
            _console.print(_render_agents())
            _console.print(_render_room_list(rooms))
            _console.print(_render_chat(room, name))
            last_render = now

        try:
            _console.print(input_prompt, end="")
            line = input()
        except (EOFError, KeyboardInterrupt):
            _console.print("\n[dim]Bye.[/dim]")
            break

        if line.strip():
            ok = _send(room, name, line.strip())
            if not ok:
                _console.print("[red]send failed[/red]")
            else:
                # Optimistic local echo — poller will confirm in 2s
                with _lock:
                    _messages.append({
                        "from_name": name,
                        "content": line.strip(),
                        "message_type": "chat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    if len(_messages) > MAX_MSG:
                        _messages.pop(0)

if __name__ == "__main__":
    main()
