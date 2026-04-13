#!/usr/bin/env python3
"""
murmur/tui_hub.py — Interactive TUI hub for Murmur.

`murmur begin` opens this full-screen Rich terminal UI. On first launch,
it prompts for name, relay URL, and secret — writes config — then enters
the hub directly. No pre-existing config needed.

Keyboard shortcuts:
  Up/Down     — switch rooms
  Enter       — send typed message
  n           — create new room (prompts for name)
  Ctrl+C      — quit
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    print("httpx not installed — pip install httpx")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.text import Text
except ImportError:
    print("rich not installed — pip install rich")
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────────
POLL_S = 2
MAX_MSG = 40
CONFIG_DIR = Path.home() / ".murmur"
LEGACY_CONFIG_DIR = Path.home() / "mcp-tunnel"  # Previous default
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_RELAY = "http://localhost:8080"
PUBLIC_RELAY = "https://murmur-ai.dev"  # Fallback public relay

# Accent palette for senders
_ACCENT_COLORS = [
    "#14b8a6", "bright_magenta", "bright_yellow", "bright_green",
    "bright_blue", "bright_red", "orange3", "medium_purple1",
]
_sender_color_cache: dict[str, str] = {}
_color_index = 0
_color_lock = threading.Lock()


def _sender_color(name: str) -> str:
    global _color_index
    with _color_lock:
        if name not in _sender_color_cache:
            _sender_color_cache[name] = _ACCENT_COLORS[_color_index % len(_ACCENT_COLORS)]
            _color_index += 1
    return _sender_color_cache[name]


# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> Optional[dict]:
    """Return config dict if it exists, else None. Checks legacy path too."""
    config_file = CONFIG_FILE
    if not config_file.exists():
        # Check legacy path
        legacy_file = LEGACY_CONFIG_DIR / "config.json"
        if legacy_file.exists():
            config_file = legacy_file
        else:
            return None
    try:
        return json.loads(config_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_config(
    name: str,
    relay_url: str,
    secret: str = "",
    api_key: str = "",
) -> None:
    CONFIG_DIR.mkdir(exist_ok=True)
    config = {
        "relay_url": relay_url.rstrip("/"),
        "instance_name": name,
        "relay_secret": secret,
        "api_key": api_key,
        "poll_mode": "sse",
        "push_notification_method": "notifications/claude/channel",
        "push_notification_channel": "mcp-tunnel",
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    CONFIG_FILE.chmod(0o600)


def _signup(relay_url: str, name: str, workspace: str) -> dict | None:
    """Call the self-service signup endpoint. Returns response dict or None on error."""
    try:
        r = httpx.post(
            f"{relay_url.rstrip('/')}/v1/auth/signup",
            json={"name": name, "workspace": workspace},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 409:
            return {"error": "workspace_taken"}
        elif r.status_code == 429:
            return {"error": "rate_limited"}
        else:
            return {"error": f"status_{r.status_code}"}
    except Exception:
        return None


def _try_connect(url: str, timeout: float = 3) -> bool:
    """Test if a relay is reachable."""
    try:
        r = httpx.get(f"{url.rstrip('/')}/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _first_launch_setup(console: Console) -> dict:
    """Interactive first-launch wizard. Returns config dict."""
    console.print()
    console.print(Panel(
        Text.from_markup(
            "  [bold #14b8a6]murmur[/bold #14b8a6]"
            "  [dim]·[/dim]"
            "  [dim]agent coordination hub[/dim]  "
        ),
        border_style="dim",
        padding=(0, 1),
    ))
    console.print()
    console.print("  Welcome! Let's get you coordinating with other agents.\n")

    # Name — conversational, plain English
    console.print("  [dim]First, pick a name that others will see in rooms.[/dim]\n")
    while True:
        name = Prompt.ask("  What should we call you?").strip()
        if not name:
            console.print("  [dim]Name can't be empty — try again.[/dim]")
            continue
        if len(name) > 64:
            console.print("  [dim]Keep it under 64 characters.[/dim]")
            continue
        if not re.match(r"^[A-Za-z0-9_\-]+$", name):
            console.print(
                "  [dim]Use letters, numbers, hyphens, or underscores — no spaces.[/dim]"
            )
            continue
        break

    console.print(f"\n  [green]Nice to meet you, {name}![/green]\n")

    # Auto-detect relay — try local first, then offer options
    console.print("  [dim]Looking for a relay...[/dim]", end="")
    relay_url = DEFAULT_RELAY
    secret = ""
    api_key = ""
    relay_ok = False

    # Try local relay first
    if _try_connect(DEFAULT_RELAY):
        console.print(f" [green]found local relay at {DEFAULT_RELAY}[/green]")
        relay_url = DEFAULT_RELAY
        relay_ok = True
    else:
        console.print(" [dim]no local relay.[/dim]")
        console.print()

        # Offer options: local, custom, or signup
        console.print("  [dim]How would you like to connect?[/dim]\n")
        console.print("    [bold]1[/bold] Start a local relay (murmur relay)")
        console.print("    [bold]2[/bold] Connect to a relay (I have a URL + secret/API key)")
        console.print("    [bold]3[/bold] Sign up for a new account on a hosted relay")
        console.print()

        choice = Prompt.ask(
            "  Which one?",
            choices=["1", "2", "3"],
            default="1",
        )

        if choice == "2":
            # Connect with existing credentials
            relay_url = Prompt.ask(
                "  Relay URL"
            ).strip().rstrip("/")

            auth_type = Prompt.ask(
                "  Auth type",
                choices=["secret", "api-key", "none"],
                default="secret",
            )

            if auth_type == "secret":
                secret = Prompt.ask("  Secret").strip()
            elif auth_type == "api-key":
                api_key = Prompt.ask("  API key (mct_...)").strip()

            console.print()
            console.print(f"  Connecting to {relay_url}...", end="")
            if _try_connect(relay_url):
                console.print(" [bold green]✓[/bold green]")
                relay_ok = True
            else:
                console.print(" [yellow]not reachable[/yellow]")

        elif choice == "3":
            # Self-service signup
            relay_url = Prompt.ask(
                "  Relay URL",
                default=PUBLIC_RELAY,
            ).strip().rstrip("/")

            console.print()
            console.print(f"  Connecting to {relay_url}...", end="")
            if not _try_connect(relay_url):
                console.print(" [yellow]not reachable[/yellow]")
                console.print(
                    f"\n  [dim]Couldn't reach {relay_url}. Check the URL and try again.[/dim]"
                )
            else:
                console.print(" [bold green]✓[/bold green]")
                console.print()

                # Get workspace name
                console.print("  [dim]Pick a workspace name (like a team or project name).[/dim]\n")
                while True:
                    workspace = Prompt.ask("  Workspace").strip().lower()
                    if not workspace:
                        console.print("  [dim]Workspace can't be empty.[/dim]")
                        continue
                    if len(workspace) < 2 or len(workspace) > 64:
                        console.print("  [dim]Keep it between 2-64 characters.[/dim]")
                        continue
                    ws_re = r"^[a-z0-9][a-z0-9_\-]*[a-z0-9]$"
                    if not re.match(ws_re, workspace) and len(workspace) > 1:
                        console.print(
                            "  [dim]Use lowercase letters, numbers, hyphens. "
                            "Start/end with letter or number.[/dim]"
                        )
                        continue
                    break

                console.print()
                console.print("  Creating account...", end="")

                result = _signup(relay_url, name, workspace)

                if result is None:
                    console.print(" [red]failed[/red]")
                    console.print(
                        "\n  [dim]Could not reach signup endpoint. "
                        "Is this a production relay?[/dim]"
                    )
                elif "error" in result:
                    console.print(" [red]failed[/red]")
                    if result["error"] == "workspace_taken":
                        console.print(
                            f"\n  [yellow]Workspace '{workspace}' is already taken. "
                            "Try another name.[/yellow]"
                        )
                    elif result["error"] == "rate_limited":
                        console.print(
                            "\n  [yellow]Too many signups. Try again in an hour.[/yellow]"
                        )
                    else:
                        console.print(f"\n  [dim]Error: {result['error']}[/dim]")
                else:
                    console.print(" [bold green]✓[/bold green]")
                    api_key = result.get("api_key", "")
                    relay_ok = True

                    console.print()
                    console.print(
                        f"  [green]Account created![/green] Workspace: [bold]{workspace}[/bold]\n"
                    )
                    console.print(
                        "  [yellow]Save your API key — it won't be shown again:[/yellow]\n"
                    )
                    console.print(f"    [bold]{api_key}[/bold]\n")

    # If still not connected and chose local, guide them
    if not relay_ok and relay_url == DEFAULT_RELAY:
        console.print()
        console.print(
            "  [dim]No relay running yet. Start one in another terminal:[/dim]\n"
        )
        console.print(
            "    [bold #14b8a6]murmur relay[/bold #14b8a6]\n"
        )
        console.print(
            "  [dim]Then run [bold]murmur begin[/bold] again. Config saved.[/dim]"
        )
    elif not relay_ok and choice != "3":
        console.print()
        console.print(
            f"  [dim]Couldn't reach {relay_url}.[/dim]\n"
            "  [dim]Config saved — run [bold]murmur begin[/bold] when the relay is up.[/dim]"
        )

    # Save config
    _write_config(name, relay_url, secret=secret, api_key=api_key)

    if relay_ok:
        console.print()
        console.print(
            f"  [green]You're in![/green] Connected as [bold]{name}[/bold].\n"
        )
        console.print(
            "  [dim]Type a message to chat, or [bold]help[/bold] for commands.[/dim]\n"
        )
        time.sleep(0.3)

    return {
        "relay_url": relay_url,
        "instance_name": name,
        "relay_secret": secret,
        "api_key": api_key,
    }


# ── API helpers ───────────────────────────────────────────────────────────────

# Cache for exchanged JWT tokens (api_key -> jwt)
_jwt_cache: dict[str, str] = {}


def _exchange_api_key_for_jwt(relay: str, api_key: str) -> str | None:
    """Exchange an API key for a JWT token."""
    if api_key in _jwt_cache:
        return _jwt_cache[api_key]

    try:
        r = httpx.post(
            f"{relay.rstrip('/')}/v1/auth/token",
            json={"api_key": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            jwt = r.json().get("token")
            if jwt:
                _jwt_cache[api_key] = jwt
                return jwt
    except Exception:
        pass
    return None


def _get_auth_token(relay: str, secret: str) -> str:
    """Get the auth token to use. Exchanges API keys for JWTs if needed."""
    if not secret:
        return ""

    # If it looks like an API key, exchange for JWT
    if secret.startswith("mct_"):
        jwt = _exchange_api_key_for_jwt(relay, secret)
        if jwt:
            return jwt
        # Fall back to using API key directly (will fail auth but gives better error)
        return secret

    # Otherwise use as-is (legacy secret or already a JWT)
    return secret


def _auth_headers(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"} if secret else {}


def _fetch_rooms(relay: str, secret: str) -> list[dict]:
    try:
        r = httpx.get(f"{relay}/rooms", headers=_auth_headers(secret), timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else data.get("rooms", [])
        return []
    except Exception:
        return []


def _fetch_history(relay: str, secret: str, room: str) -> list[dict]:
    try:
        r = httpx.get(
            f"{relay}/rooms/{room}/history",
            headers=_auth_headers(secret),
            params={"limit": MAX_MSG},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else data.get("messages", [])
        return []
    except Exception:
        return []


def _send_message(relay: str, secret: str, room: str, sender: str, content: str) -> bool:
    try:
        r = httpx.post(
            f"{relay}/rooms/{room}/messages",
            headers=_auth_headers(secret),
            json={"from_name": sender, "content": content, "message_type": "chat"},
            timeout=5,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


def _join_room(relay: str, secret: str, room: str, participant: str) -> None:
    try:
        httpx.post(
            f"{relay}/rooms/{room}/join",
            headers=_auth_headers(secret),
            json={"participant": participant},
            timeout=5,
        )
    except Exception:
        pass


def _create_room(relay: str, secret: str, name: str, creator: str) -> Optional[dict]:
    try:
        r = httpx.post(
            f"{relay}/rooms",
            headers=_auth_headers(secret),
            json={"name": name, "created_by": creator},
            timeout=5,
        )
        if r.status_code in (200, 201):
            return r.json()
        return None
    except Exception:
        return None


# ── Shared state (thread-safe) ────────────────────────────────────────────────

class HubState:
    """All mutable state shared between the polling thread and render loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rooms: list[dict] = []
        self.messages: list[dict] = []
        self.selected_room_idx: int = 0
        self.status: str = "Connecting..."
        self.connected: bool = False
        self.status_bar_msg: str = ""

    # Rooms
    def set_rooms(self, rooms: list[dict]) -> None:
        with self._lock:
            self.rooms = rooms

    def get_rooms(self) -> list[dict]:
        with self._lock:
            return list(self.rooms)

    def get_selected_room(self) -> Optional[dict]:
        with self._lock:
            if not self.rooms:
                return None
            idx = max(0, min(self.selected_room_idx, len(self.rooms) - 1))
            return self.rooms[idx]

    def select_next(self) -> None:
        with self._lock:
            if self.rooms:
                self.selected_room_idx = (self.selected_room_idx + 1) % len(self.rooms)

    def select_prev(self) -> None:
        with self._lock:
            if self.rooms:
                self.selected_room_idx = (self.selected_room_idx - 1) % len(self.rooms)

    def select_by_name(self, name: str) -> None:
        with self._lock:
            for i, r in enumerate(self.rooms):
                if r.get("name") == name:
                    self.selected_room_idx = i
                    return

    # Messages
    def set_messages(self, msgs: list[dict]) -> None:
        with self._lock:
            self.messages = msgs[-MAX_MSG:]

    def get_messages(self) -> list[dict]:
        with self._lock:
            return list(self.messages)

    def append_message(self, msg: dict) -> None:
        with self._lock:
            self.messages.append(msg)
            if len(self.messages) > MAX_MSG:
                self.messages.pop(0)

    # Connection
    def set_connected(self, connected: bool, status: str) -> None:
        with self._lock:
            self.connected = connected
            self.status = status

    def get_connection(self) -> tuple[bool, str]:
        with self._lock:
            return self.connected, self.status

    # Status bar
    def set_status_bar(self, msg: str) -> None:
        with self._lock:
            self.status_bar_msg = msg

    def get_status_bar(self) -> str:
        with self._lock:
            return self.status_bar_msg


# ── Background poller ─────────────────────────────────────────────────────────

def _poll_loop(relay: str, secret: str, state: HubState, stop_event: threading.Event) -> None:
    """Poll relay for rooms + messages every POLL_S seconds."""
    while not stop_event.is_set():
        try:
            rooms = _fetch_rooms(relay, secret)
            if rooms:
                state.set_rooms(rooms)
                state.set_connected(True, "Connected")
            else:
                state.set_connected(False, "No rooms (relay may be empty)")

            selected = state.get_selected_room()
            if selected:
                room_name = selected.get("name") or selected.get("id", "")
                if room_name:
                    msgs = _fetch_history(relay, secret, room_name)
                    state.set_messages(msgs)
        except Exception:
            state.set_connected(False, "Relay unreachable")

        stop_event.wait(POLL_S)


# ── Renderers ─────────────────────────────────────────────────────────────────

def _render_header(relay_url: str, agent_name: str, connected: bool, status: str) -> Panel:
    dot = "[bold green]●[/bold green]" if connected else "[bold red]●[/bold red]"
    # Strip protocol for display brevity
    display_relay = relay_url.replace("http://", "").replace("https://", "")
    header_text = Text()
    header_text.append("  murmur", style="bold #14b8a6")
    header_text.append("  ·  ", style="dim")
    header_text.append(agent_name, style=f"bold {_sender_color(agent_name)}")
    header_text.append("  ·  ", style="dim")
    header_text.append(display_relay, style="dim")
    header_text.append("  ", style="dim")
    header_text.append_text(Text.from_markup(dot))
    return Panel(header_text, border_style="dim", padding=(0, 1))


def _render_room_list(rooms: list[dict], selected_idx: int) -> Panel:
    if not rooms:
        return Panel(
            Text("  [dim]No rooms yet. Press [bold]n[/bold] to create one.[/dim]"),
            title="[bold]Rooms[/bold]",
            border_style="dim",
        )

    t = Text()
    for i, room in enumerate(rooms):
        name = room.get("name") or room.get("id", "?")
        members = room.get("members", [])
        member_count = len(members) if isinstance(members, list) else members

        ts_raw = room.get("last_activity") or room.get("updated_at") or ""
        try:
            ts = ts_raw[:16].replace("T", " ") if ts_raw else ""
        except Exception:
            ts = ""

        is_selected = i == selected_idx
        if is_selected:
            t.append("▶ ", style="bold #14b8a6")
            t.append(f"#{name}", style="bold #14b8a6")
        else:
            t.append("  ", style="")
            t.append(f"#{name}", style="dim")

        t.append(f"  {member_count}👥", style="dim")
        if ts:
            t.append(f"  {ts}", style="dim")
        t.append("\n")

    return Panel(
        t,
        title="[bold]Rooms[/bold]",
        border_style="dim",
        padding=(0, 1),
        subtitle="[dim]↑↓ navigate  n new[/dim]",
    )


def _render_chat(messages: list[dict], room_name: str, my_name: str) -> Panel:
    if not messages:
        no_msg = Text(f"\n  [dim]No messages in #{room_name} yet. Say something![/dim]")
        return Panel(
            no_msg,
            title=f"[bold]#{room_name}[/bold]",
            border_style="#14b8a6",
            padding=(0, 1),
        )

    lines = Text()
    for msg in messages:
        sender = msg.get("from_name") or msg.get("sender", "?")
        content = msg.get("content", "")
        ts_raw = msg.get("timestamp", "")
        mtype = msg.get("message_type", "chat")

        try:
            ts = ts_raw[:16].replace("T", " ")
        except Exception:
            ts = "??:??"

        lines.append(f"[{ts}] ", style="dim")

        color = _sender_color(sender)
        is_me = sender == my_name
        sender_style = f"bold {color}" + (" on grey15" if is_me else "")
        lines.append(sender, style=sender_style)

        if mtype not in ("chat", ""):
            lines.append(f" [{mtype}]", style="dim italic")

        lines.append(": ")
        lines.append(content + "\n", style="bright_white" if is_me else "white")

    return Panel(
        lines,
        title=f"[bold]#{room_name}[/bold]",
        border_style="#14b8a6",
        padding=(0, 1),
        subtitle="[dim]Enter send  Ctrl+C quit[/dim]",
    )


def _render_input_bar(agent_name: str, status_msg: str) -> Panel:
    content = Text()
    content.append("> ", style="bold dim")
    if status_msg:
        content.append(status_msg, style="dim italic")
    else:
        content.append(
            "type a message, or: join <room> · create <name> · invite <name> · help · quit",
            style="dim",
        )
    return Panel(content, border_style="dim", padding=(0, 1))


def _print_help(console: Console) -> None:
    """Print plain-English command reference."""
    console.print()
    console.print("  [bold]Commands[/bold]\n")
    rows = [
        ("create <name>", "create a new room"),
        ("new room called <name>", "same as create"),
        ("join <room>", "switch to that room"),
        ("go to <room>", "same as join"),
        ("invite <name>", "show join token for current room"),
        ("help", "show this message"),
        ("quit / exit", "close the hub"),
        ("<anything else>", "sent as a chat message to the active room"),
    ]
    for cmd, desc in rows:
        console.print(f"  [bold #14b8a6]{cmd:<30}[/bold #14b8a6]  [dim]{desc}[/dim]")
    console.print()


# ── Main hub loop ─────────────────────────────────────────────────────────────

MURMUR_BANNER = """
[bold #14b8a6]  ███╗   ███╗ ██╗   ██╗ ██████╗  ███╗   ███╗ ██╗   ██╗ ██████╗[/bold #14b8a6]
[bold #14b8a6]  ████╗ ████║ ██║   ██║ ██╔══██╗ ████╗ ████║ ██║   ██║ ██╔══██╗[/bold #14b8a6]
[bold #14b8a6]  ██╔████╔██║ ██║   ██║ ██████╔╝ ██╔████╔██║ ██║   ██║ ██████╔╝[/bold #14b8a6]
[bold #14b8a6]  ██║╚██╔╝██║ ██║   ██║ ██╔══██╗ ██║╚██╔╝██║ ██║   ██║ ██╔══██╗[/bold #14b8a6]
[bold #14b8a6]  ██║ ╚═╝ ██║ ╚██████╔╝ ██║  ██║ ██║ ╚═╝ ██║ ╚██████╔╝ ██║  ██║[/bold #14b8a6]
[bold #14b8a6]  ╚═╝     ╚═╝  ╚═════╝  ╚═╝  ╚═╝ ╚═╝     ╚═╝  ╚═════╝  ╚═╝  ╚═╝[/bold #14b8a6]

[dim]  Agent coordination relay  ·  v0.3.1[/dim]
"""


def run_hub() -> None:
    console = Console()

    # Show banner
    console.print(MURMUR_BANNER)

    # 1. Load or create config
    cfg = _load_config()
    if cfg is None:
        cfg = _first_launch_setup(console)

    relay_url: str = cfg.get("relay_url", DEFAULT_RELAY).rstrip("/")
    agent_name: str = cfg.get("instance_name", "agent")
    raw_secret: str = cfg.get("relay_secret", "") or cfg.get("api_key", "")

    # Exchange API key for JWT if needed
    secret = _get_auth_token(relay_url, raw_secret)

    # 2. Initial room load
    console.print(f"\n  [dim]Connecting to {relay_url}...[/dim]")
    rooms = _fetch_rooms(relay_url, secret)
    if not rooms:
        # Offer to create a default room for first-time users
        console.print("  [dim]No rooms yet.[/dim]")
        console.print()
        create_default = Prompt.ask(
            "  Create a room to get started?",
            choices=["y", "n"],
            default="y",
        )
        if create_default.lower() == "y":
            room_name = Prompt.ask(
                "  Room name",
                default="general",
            ).strip()
            if room_name and re.match(r"^[A-Za-z0-9_\-]+$", room_name):
                result = _create_room(relay_url, secret, room_name, agent_name)
                if result:
                    console.print(f"  [green]Room '{room_name}' created![/green]")
                    rooms = _fetch_rooms(relay_url, secret)
                else:
                    console.print("  [yellow]Couldn't create room — name taken?[/yellow]")
        if not rooms:
            console.print(
                "  [dim]Type [bold]create <name>[/bold] anytime to make a room.[/dim]"
            )

    # 3. Set up shared state
    state = HubState()
    state.set_rooms(rooms)
    if rooms:
        state.set_connected(True, "Connected")
        # Auto-join first room
        first_room = rooms[0].get("name") or rooms[0].get("id", "")
        if first_room:
            _join_room(relay_url, secret, first_room, agent_name)
            msgs = _fetch_history(relay_url, secret, first_room)
            state.set_messages(msgs)
    else:
        state.set_connected(False, "No rooms yet")

    # 4. Start background poller
    stop_event = threading.Event()
    poller = threading.Thread(
        target=_poll_loop,
        args=(relay_url, secret, state, stop_event),
        daemon=True,
    )
    poller.start()

    # 5. Main interactive loop
    # Rich Live doesn't support input() — we use clear+redraw + input() pattern
    # (same approach as murmur_tui.py, which proven to work)
    console.clear()
    last_render = 0.0

    try:
        while True:
            now = time.monotonic()

            # Redraw if poll interval passed or first render
            if now - last_render >= POLL_S:
                connected, conn_status = state.get_connection()
                rooms_snap = state.get_rooms()
                selected = state.get_selected_room()
                room_name = (
                    (selected.get("name") or selected.get("id", "general"))
                    if selected else "general"
                )
                msgs_snap = state.get_messages()
                status_bar = state.get_status_bar()

                console.clear()
                console.print(_render_header(relay_url, agent_name, connected, conn_status))
                console.print()

                # Side-by-side: rooms (left) and chat (right) — approximated with stacked layout
                # Rich Layout requires Live; in non-Live mode we stack vertically for compatibility
                # with wide terminals we use a simple two-column trick via columns
                selected_idx: int
                with state._lock:
                    selected_idx = state.selected_room_idx

                console.print(_render_room_list(rooms_snap, selected_idx))
                console.print()
                console.print(_render_chat(msgs_snap, room_name, agent_name))
                console.print()
                console.print(_render_input_bar(agent_name, status_bar))
                last_render = now

            # Prompt for input
            try:
                line = input(f"[{agent_name}]> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not line:
                continue

            # ── Plain-English command parsing ──────────────────────────────────
            cmd = line.strip()
            cmd_lower = cmd.lower()

            # quit / exit
            if cmd_lower in ("quit", "exit", "q"):
                break

            # help
            if cmd_lower == "help":
                _print_help(console)
                last_render = 0
                continue

            # Arrow key passthrough (terminal sends escape sequences)
            if cmd == "\x1b[A" or cmd_lower == "up":
                state.select_prev()
                selected = state.get_selected_room()
                if selected:
                    rname = selected.get("name") or selected.get("id", "")
                    if rname:
                        _join_room(relay_url, secret, rname, agent_name)
                        state.set_messages(_fetch_history(relay_url, secret, rname))
                last_render = 0
                continue

            if cmd == "\x1b[B" or cmd_lower == "down":
                state.select_next()
                selected = state.get_selected_room()
                if selected:
                    rname = selected.get("name") or selected.get("id", "")
                    if rname:
                        _join_room(relay_url, secret, rname, agent_name)
                        state.set_messages(_fetch_history(relay_url, secret, rname))
                last_render = 0
                continue

            # join / go to <room>  — "join dev-room", "go to design", "switch to dev"
            join_match = re.match(
                r"^(?:join|go\s+to|switch\s+to|enter)\s+#?(\S+)$",
                cmd_lower,
            )
            if join_match:
                target = join_match.group(1)
                rooms_snap = state.get_rooms()
                match = next(
                    (r for r in rooms_snap if r.get("name", "").lower() == target),
                    None,
                )
                if match:
                    state.select_by_name(match["name"])
                    _join_room(relay_url, secret, match["name"], agent_name)
                    state.set_messages(_fetch_history(relay_url, secret, match["name"]))
                    state.set_status_bar(f"Joined #{match['name']}")
                else:
                    state.set_status_bar(f"Room '{target}' not found.")
                last_render = 0
                continue

            # create / new room called <name>
            create_match = re.match(
                r"^(?:create|new\s+room(?:\s+called)?|make(?:\s+room)?)\s+#?(\S+)$",
                cmd_lower,
            )
            # Also handle bare "n" shortcut from old UX
            if not create_match and cmd_lower == "n":
                # Prompt inline
                console.print()
                new_name = Prompt.ask("  Room name").strip()
                if new_name and re.match(r"^[A-Za-z0-9_\-]+$", new_name):
                    result = _create_room(relay_url, secret, new_name, agent_name)
                    if result:
                        state.set_status_bar(f"Room '{new_name}' created.")
                        new_rooms = _fetch_rooms(relay_url, secret)
                        state.set_rooms(new_rooms)
                        state.select_by_name(new_name)
                        _join_room(relay_url, secret, new_name, agent_name)
                        state.set_messages(_fetch_history(relay_url, secret, new_name))
                    else:
                        state.set_status_bar("Failed to create room.")
                else:
                    state.set_status_bar("Invalid name (letters, numbers, hyphens only).")
                last_render = 0
                continue

            if create_match:
                new_name_raw = create_match.group(1)
                # Sanitise: strip leading # if present
                new_name_raw = new_name_raw.lstrip("#")
                if re.match(r"^[A-Za-z0-9_\-]+$", new_name_raw):
                    result = _create_room(relay_url, secret, new_name_raw, agent_name)
                    if result:
                        state.set_status_bar(f"Room '{new_name_raw}' created.")
                        new_rooms = _fetch_rooms(relay_url, secret)
                        state.set_rooms(new_rooms)
                        state.select_by_name(new_name_raw)
                        _join_room(relay_url, secret, new_name_raw, agent_name)
                        state.set_messages(_fetch_history(relay_url, secret, new_name_raw))
                    else:
                        state.set_status_bar(f"Couldn't create '{new_name_raw}' — already exists?")
                else:
                    state.set_status_bar("Room names: letters, numbers, hyphens, underscores only.")
                last_render = 0
                continue

            # invite <name> — shows a join token for the current room
            invite_match = re.match(r"^invite\s+(\S+)$", cmd_lower)
            if invite_match:
                selected = state.get_selected_room()
                if selected:
                    rname = selected.get("name") or selected.get("id", "")
                    invitee = invite_match.group(1)
                    import base64 as _b64
                    import json as _json
                    payload = _json.dumps({"relay_url": relay_url, "secret": secret, "room": rname})
                    token = "murm_join_" + _b64.urlsafe_b64encode(payload.encode()).decode()
                    console.print()
                    console.print(f"  [bold]Invite '{invitee}' to #{rname}[/bold]")
                    console.print("  [dim]Share this token:[/dim]")
                    console.print(f"  [#14b8a6]{token}[/#14b8a6]")
                    console.print(f"  [dim]They run: murmur join {token} --name {invitee}[/dim]")
                    console.print()
                    state.set_status_bar(f"Token generated for #{rname}")
                else:
                    state.set_status_bar("No active room. Join one first.")
                last_render = 0
                continue

            # ── Default: send as chat message ──────────────────────────────────
            selected = state.get_selected_room()
            if not selected:
                state.set_status_bar("No room selected. Type 'create <name>' to make one.")
                last_render = 0
                continue

            room_name = selected.get("name") or selected.get("id", "")
            if not room_name:
                state.set_status_bar("Could not determine room name.")
                last_render = 0
                continue

            ok = _send_message(relay_url, secret, room_name, agent_name, cmd)
            if ok:
                state.set_status_bar("")
                # Optimistic local echo
                state.append_message({
                    "from_name": agent_name,
                    "content": cmd,
                    "message_type": "chat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            else:
                state.set_status_bar(
                    "Couldn't reach the relay. Is it running? "
                    "Try: RELAY_SECRET=x python -m murmur.relay"
                )

            last_render = 0  # force immediate redraw after send

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        console.print("\n[dim]Murmur Hub closed. Goodbye.[/dim]")
