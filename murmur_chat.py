#!/usr/bin/env python3
"""
murmur_chat.py — Beautiful group chat for the Murmur relay.

Usage:
    python murmur_chat.py                    # uses ~/mcp-tunnel/config.json
    python murmur_chat.py arav               # name override
    python murmur_chat.py arav test-room     # name + room override

For the full hub with room management, use:
    murmur begin
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Deps ──────────────────────────────────────────────────────────────────────

try:
    import httpx
except ImportError:
    print("httpx not installed — pip install httpx")
    sys.exit(1)

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("rich not installed — pip install rich")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_FILE = Path.home() / "mcp-tunnel" / "config.json"

_ACCENT_COLORS = [
    "bright_cyan",
    "bright_magenta",
    "bright_yellow",
    "bright_green",
    "orchid1",
    "orange3",
    "sky_blue1",
    "medium_spring_green",
]
_sender_colors: dict[str, str] = {}
_color_idx = 0
_color_lock = threading.Lock()


def _color(name: str) -> str:
    global _color_idx
    with _color_lock:
        if name not in _sender_colors:
            _sender_colors[name] = _ACCENT_COLORS[_color_idx % len(_ACCENT_COLORS)]
            _color_idx += 1
    return _sender_colors[name]


def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def _resolve_params(args: list[str]) -> tuple[str, str, str, str]:
    """Return (name, room, relay_url, secret) from args + config."""
    cfg = _load_config()
    name = args[0] if len(args) > 0 else cfg.get("instance_name", "agent")
    room = args[1] if len(args) > 1 else "test-room"
    relay = args[2] if len(args) > 2 else cfg.get("relay_url", "http://localhost:8080")
    secret = args[3] if len(args) > 3 else cfg.get("relay_secret", "")
    return name, room, relay.rstrip("/"), secret


# ── API ───────────────────────────────────────────────────────────────────────

def _headers(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"} if secret else {}


def _fetch_history(relay: str, secret: str, room: str, limit: int = 50) -> list[dict]:
    try:
        r = httpx.get(
            f"{relay}/rooms/{room}/history",
            headers=_headers(secret),
            params={"limit": limit},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else data.get("messages", [])
    except Exception:
        pass
    return []


def _send(relay: str, secret: str, room: str, name: str, content: str) -> bool:
    try:
        r = httpx.post(
            f"{relay}/rooms/{room}/messages",
            headers=_headers(secret),
            json={"from_name": name, "content": content, "message_type": "chat"},
            timeout=5,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


def _join(relay: str, secret: str, room: str, name: str) -> None:
    try:
        httpx.post(
            f"{relay}/rooms/{room}/join",
            headers=_headers(secret),
            json={"participant": name},
            timeout=5,
        )
    except Exception:
        pass


def _health_check(relay: str, secret: str) -> bool:
    try:
        r = httpx.get(f"{relay}/health", headers=_headers(secret), timeout=4)
        return r.status_code == 200
    except Exception:
        return False


# ── Render ────────────────────────────────────────────────────────────────────

def _format_ts(raw: str) -> str:
    try:
        return raw[11:16] if "T" in raw else raw[:5]
    except Exception:
        return "??:??"


def _render_messages(msgs: list[dict], my_name: str, room: str) -> Panel:
    if not msgs:
        t = Text(f"\n  No messages in #{room} yet.\n", style="dim")
        return Panel(t, title=f"[bold bright_cyan]#{room}[/bold bright_cyan]",
                     border_style="bright_cyan", padding=(0, 2))

    t = Text()
    for msg in msgs[-30:]:
        sender = msg.get("from_name") or msg.get("sender", "?")
        content = msg.get("content", "")
        ts = _format_ts(msg.get("timestamp", ""))
        mtype = msg.get("message_type", "chat")

        t.append(f" {ts} ", style="dim")
        color = _color(sender)
        is_me = sender == my_name
        t.append(sender, style=f"bold {color}" + (" reverse" if is_me else ""))
        if mtype not in ("chat", ""):
            t.append(f" [{mtype}]", style="dim italic")
        t.append(":  ")
        t.append(content + "\n", style="bright_white" if is_me else "white")

    return Panel(
        t,
        title=f"[bold bright_cyan]#{room}[/bold bright_cyan]",
        border_style="bright_cyan",
        padding=(0, 1),
        subtitle="[dim]Enter to send  /rooms  /join <room>  /quit[/dim]",
    )


def _render_status(relay: str, name: str, connected: bool) -> Text:
    dot = "[bold green]●[/bold green]" if connected else "[bold red]●[/bold red]"
    t = Text.from_markup(
        f"  [bold bright_cyan]murmur[/bold bright_cyan]"
        f"  [dim]·[/dim]  [bold {_color(name)}]{name}[/bold {_color(name)}]"
        f"  [dim]·[/dim]  [dim]{relay.replace('http://', '').replace('https://', '')}[/dim]"
        f"  {dot}"
    )
    return t


# ── Poll thread ───────────────────────────────────────────────────────────────

class _State:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.msgs: list[dict] = []
        self.connected = False
        self.status = "Connecting..."

    def update_msgs(self, msgs: list[dict]) -> None:
        with self._lock:
            self.msgs = msgs

    def get_msgs(self) -> list[dict]:
        with self._lock:
            return list(self.msgs)

    def set_connected(self, v: bool, s: str) -> None:
        with self._lock:
            self.connected = v
            self.status = s

    def get_connected(self) -> tuple[bool, str]:
        with self._lock:
            return self.connected, self.status


def _poll(relay: str, secret: str, room: str, state: _State, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            msgs = _fetch_history(relay, secret, room)
            state.update_msgs(msgs)
            state.set_connected(True, "live")
        except Exception:
            state.set_connected(False, "relay unreachable")
        stop.wait(2)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    console = Console()
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        console.print(__doc__)
        return

    name, room, relay, secret = _resolve_params(args)

    # Splash
    console.clear()
    console.print()
    console.print(Panel(
        Text.from_markup(
            "  [bold bright_cyan]murmur[/bold bright_cyan]"
            "  [dim]group chat[/dim]  "
        ),
        border_style="dim",
        padding=(0, 2),
    ))
    console.print(f"\n  [dim]Connecting to[/dim] [bright_cyan]{relay}[/bright_cyan]"
                  f"  [dim]as[/dim] [bold {_color(name)}]{name}[/bold {_color(name)}]"
                  f"  [dim]→[/dim] [bold]#{room}[/bold]\n")

    # Health check
    online = _health_check(relay, secret)
    if online:
        console.print("  [bold green]✓[/bold green] Relay online\n")
    else:
        console.print("  [yellow]⚠  Relay not reachable.[/yellow]")
        console.print("  [dim]Start the relay in another terminal:[/dim]")
        console.print("  [bright_cyan]  murmur relay[/bright_cyan]\n")

    # Join room
    _join(relay, secret, room, name)

    # Initial history
    state = _State()
    msgs = _fetch_history(relay, secret, room)
    state.update_msgs(msgs)
    state.set_connected(online, "live" if online else "offline")

    # Start poller
    stop_event = threading.Event()
    t = threading.Thread(target=_poll, args=(relay, secret, room, state, stop_event), daemon=True)
    t.start()

    # Chat loop
    try:
        while True:
            # Redraw
            console.clear()
            connected, conn_status = state.get_connected()
            console.print(Panel(
                _render_status(relay, name, connected), border_style="dim", padding=(0, 1)
            ))
            console.print()
            console.print(_render_messages(state.get_msgs(), name, room))
            console.print()

            # Input
            try:
                line = input(f" {name}> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not line:
                continue

            # Commands
            if line.lower() in ("/quit", "/exit", "quit", "exit"):
                break

            if line.lower() == "/help":
                console.print(
                    "\n  [bold]Commands:[/bold]  /join <room>  /rooms  /quit\n"
                    "  Everything else is sent as a chat message.\n"
                )
                time.sleep(1.5)
                continue

            if line.lower() == "/rooms":
                try:
                    resp = httpx.get(f"{relay}/rooms", headers=_headers(secret), timeout=5)
                    rooms_data = resp.json()
                    if isinstance(rooms_data, list):
                        table = Table(
                            box=box.SIMPLE_HEAD, show_header=True, header_style="bold dim"
                        )
                        table.add_column("Room", style="bright_cyan")
                        table.add_column("Members", style="dim")
                        for r in rooms_data:
                            rname = r.get("name") or r.get("id", "?")
                            members = r.get("members", [])
                            cnt = str(len(members)) if isinstance(members, list) else str(members)
                            table.add_row(f"#{rname}", cnt)
                        console.print()
                        console.print(table)
                        time.sleep(2)
                except Exception as e:
                    console.print(f"  [red]Error: {e}[/red]")
                    time.sleep(1.5)
                continue

            if line.lower().startswith("/join "):
                room = line.split(None, 1)[1].lstrip("#").strip()
                _join(relay, secret, room, name)
                state.update_msgs(_fetch_history(relay, secret, room))
                continue

            # Send message
            ok = _send(relay, secret, room, name, line)
            if ok:
                # Optimistic echo
                state.update_msgs(
                    state.get_msgs() + [{
                        "from_name": name,
                        "content": line,
                        "message_type": "chat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }]
                )
            else:
                console.print("  [red]Send failed — is the relay running?[/red]")
                time.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        console.print("\n  [dim]Disconnected. Goodbye.[/dim]\n")


if __name__ == "__main__":
    main()
