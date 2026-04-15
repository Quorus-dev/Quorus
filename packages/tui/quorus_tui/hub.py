#!/usr/bin/env python3
"""
quorus/tui_hub.py — Interactive TUI hub for Quorus.

`quorus begin` opens this full-screen Rich terminal UI. On first launch,
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
# SSE push is the primary delivery path — poll is a slow reconciliation fallback
# so the TUI still catches up if the SSE stream drops (message IDs are de-duped).
POLL_S = 10
MAX_MSG = 40
SSE_RECONNECT_S = 2  # backoff between dropped-stream reconnects


def _resolve_config_dir() -> Path:
    """Honor QUORUS_CONFIG_DIR / MCP_TUNNEL_CONFIG_DIR — same precedence
    as the CLI's ``_config_dir()`` helper. Without this the TUI silently
    reads ~/.quorus/config.json regardless of what the calling shell set,
    which makes multi-profile / teammate testing impossible."""
    import os as _os

    override = (
        _os.environ.get("QUORUS_CONFIG_DIR")
        or _os.environ.get("MCP_TUNNEL_CONFIG_DIR")
    )
    if override:
        return Path(override).expanduser()
    return Path.home() / ".quorus"


CONFIG_DIR = _resolve_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_RELAY = "http://localhost:8080"
PUBLIC_RELAY = "https://quorus.dev"  # Fallback public relay

# Accent palette for senders — teal/amber/purple only, matches ui.py spec
_ACCENT_COLORS = [
    "#14b8a6",  # teal (primary)
    "#a78bfa",  # purple (agent)
    "#fbbf24",  # amber (room)
    "#2dd4bf",  # teal-light
    "#5eead4",  # mint
    "#f59e0b",  # warning amber
    "#10b981",  # success
    "#0d9488",  # teal-deep
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
    """Return config dict if it exists, else None."""
    if not CONFIG_FILE.exists():
        return None
    try:
        return json.loads(CONFIG_FILE.read_text())
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
        "push_notification_channel": "quorus",
    }
    # Atomic 0600 write — no TOCTOU window between write and chmod.
    import os as _os
    flags = _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC
    fd = _os.open(str(CONFIG_FILE), flags, 0o600)
    with _os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)


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
    try:
        from quorus_cli import ui as _ui
        _ui.banner()
        _ui.info("Let's get you connected to a swarm. This takes ~30 seconds.")
        console.print()
    except Exception:
        # Fallback if ui module unavailable
        console.print()
        console.print(Panel(
            Text.from_markup(
                "  [bold #14b8a6]quorus[/bold #14b8a6]"
                "  [dim]·[/dim]"
                "  [dim]coordination for agent swarms[/dim]  "
            ),
            border_style="#14b8a6",
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

        # Offer options: paste invite token (most common for teammates),
        # local relay (solo), bring-your-own URL+secret, or sign up.
        console.print("  [dim]How would you like to connect?[/dim]\n")
        console.print(
            "    [bold]1[/bold] Paste an invite token "
            "(someone shared a [cyan]quorus://[/cyan] link with me)"
        )
        console.print("    [bold]2[/bold] Start a local relay (solo / testing)")
        console.print("    [bold]3[/bold] Connect to a relay (I have a URL + secret/API key)")
        console.print("    [bold]4[/bold] Sign up for a new account on a hosted relay")
        console.print()

        choice = Prompt.ask(
            "  Which one?",
            choices=["1", "2", "3", "4"],
            default="1",
        )

        if choice == "1":
            # Paste invite token — most common teammate onboarding flow.
            import base64 as _b64
            import json as _json

            token = Prompt.ask(
                "  Paste the [cyan]quorus://[/cyan] invite token"
            ).strip()
            # Accept bare token, quorus://... form, and legacy murm_join_
            raw = token
            decoded: dict = {}
            try:
                if raw.startswith("quorus://"):
                    raw = raw[len("quorus://"):]
                    decoded = _json.loads(_b64.urlsafe_b64decode(raw.encode()).decode())
                    relay_url = decoded.get("r", "").rstrip("/")
                    secret = decoded.get("s", "")
                    api_key = decoded.get("k", "")
                    joined_room = decoded.get("n", "")
                elif raw.startswith("quorus_join_") or raw.startswith("murm_join_"):
                    prefix = "quorus_join_" if raw.startswith("quorus_join_") else "murm_join_"
                    payload = _json.loads(
                        _b64.urlsafe_b64decode(raw[len(prefix):].encode()).decode()
                    )
                    relay_url = payload.get("relay_url", "").rstrip("/")
                    secret = payload.get("secret", "")
                    joined_room = payload.get("room", "")
                else:
                    console.print("  [yellow]Token format not recognized — "
                                  "falling back to manual URL entry.[/yellow]")
                    decoded = {}
            except Exception as exc:
                console.print(f"  [yellow]Couldn't decode token: {exc}[/yellow]")
                decoded = {}

            if decoded and relay_url:
                console.print(f"\n  Connecting to {relay_url}...", end="")
                if _try_connect(relay_url):
                    console.print(" [bold green]✓[/bold green]")
                    relay_ok = True
                    if joined_room:
                        console.print(
                            f"  [dim]Token grants access to "
                            f"[cyan]#{joined_room}[/cyan]. "
                            "You'll be auto-joined on first message.[/dim]"
                        )
                else:
                    console.print(" [yellow]not reachable[/yellow]")

        elif choice == "3":
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

        elif choice == "4":
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
            "    [bold #14b8a6]quorus relay[/bold #14b8a6]\n"
        )
        console.print(
            "  [dim]Then run [bold]quorus begin[/bold] again. Config saved.[/dim]"
        )
    elif not relay_ok and choice != "3":
        console.print()
        console.print(
            f"  [dim]Couldn't reach {relay_url}.[/dim]\n"
            "  [dim]Config saved — run [bold]quorus begin[/bold] when the relay is up.[/dim]"
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


def _fetch_history(relay: str, secret: str, room: str) -> list[dict] | None:
    """Fetch room history. Returns list on success, None on error.

    None is a sentinel for 'couldn't fetch' so the caller can preserve
    existing state rather than clobbering it with []. Without this,
    transient 429s or network blips wipe the user's chat pane.
    """
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
        return None
    except Exception:
        return None


def _load_history_into(state: "HubState", relay: str, secret: str, room: str) -> None:
    """Fetch and apply room history, leaving existing state intact on failure."""
    msgs = _fetch_history(relay, secret, room)
    if msgs is not None:
        state.set_messages(msgs)


def _send_message(
    relay: str, secret: str, room: str, sender: str, content: str,
) -> Optional[str]:
    """Send a chat message. Returns the server-assigned message ID on success,
    else None. The ID lets the caller dedup its own optimistic echo against
    the real message coming back through SSE / history."""
    try:
        r = httpx.post(
            f"{relay}/rooms/{room}/messages",
            headers=_auth_headers(secret),
            json={"from_name": sender, "content": content, "message_type": "chat"},
            timeout=5,
        )
        if r.status_code in (200, 201):
            try:
                data = r.json()
                mid = data.get("id") or data.get("message_id") or ""
                return str(mid) if mid else ""
            except (ValueError, TypeError):
                return ""
        return None
    except Exception:
        return None


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
        # Dedup set of recently-seen message IDs. Prevents SSE push + history
        # refetch from double-rendering the same message. Bounded to 2*MAX_MSG.
        self._seen_ids: list[str] = []
        self._seen_set: set[str] = set()
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
    def _track_id(self, mid: str) -> None:
        """Record a message ID in the dedup set, evicting the oldest if full."""
        if not mid or mid in self._seen_set:
            return
        self._seen_set.add(mid)
        self._seen_ids.append(mid)
        if len(self._seen_ids) > MAX_MSG * 2:
            evicted = self._seen_ids.pop(0)
            self._seen_set.discard(evicted)

    @staticmethod
    def _dedup_key(msg: dict) -> str:
        """Prefer message_id (canonical, shared across SSE/history) over id
        (per-delivery UUID in fan-out payloads)."""
        return str(msg.get("message_id") or msg.get("id") or "")

    def set_messages(self, msgs: list[dict]) -> None:
        with self._lock:
            self.messages = msgs[-MAX_MSG:]
            # Rebuild dedup set from the authoritative history snapshot
            self._seen_ids.clear()
            self._seen_set.clear()
            for m in self.messages:
                self._track_id(self._dedup_key(m))

    def get_messages(self) -> list[dict]:
        with self._lock:
            return list(self.messages)

    def append_message(self, msg: dict) -> None:
        with self._lock:
            key = self._dedup_key(msg)
            if key and key in self._seen_set:
                return  # already rendered
            self.messages.append(msg)
            if len(self.messages) > MAX_MSG:
                self.messages.pop(0)
            self._track_id(key)

    def selected_room_name(self) -> str:
        """Return the currently-selected room name, or empty string."""
        with self._lock:
            if not self.rooms:
                return ""
            idx = max(0, min(self.selected_room_idx, len(self.rooms) - 1))
            r = self.rooms[idx]
            return r.get("name") or r.get("id") or ""

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
    """Slow reconciliation poll — SSE push handles real-time delivery. This
    exists only to recover from dropped streams and reconcile missed messages."""
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
                    if msgs is not None:
                        state.set_messages(msgs)
        except Exception:
            state.set_connected(False, "Relay unreachable")

        stop_event.wait(POLL_S)


# ── Real-time push (SSE) ──────────────────────────────────────────────────────

def _mint_sse_token(relay: str, secret: str, recipient: str) -> Optional[str]:
    """Exchange bearer auth for a short-lived SSE stream token."""
    try:
        r = httpx.post(
            f"{relay}/stream/token",
            headers=_auth_headers(secret),
            json={"recipient": recipient},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("token")
    except Exception:
        pass
    return None


def _sse_loop(
    relay: str,
    secret: str,
    recipient: str,
    state: HubState,
    stop_event: threading.Event,
) -> None:
    """Subscribe to SSE push and apply messages as they arrive.

    Runs in a daemon thread. On disconnect (network blip, relay restart,
    token expiry) it reconnects with a short backoff. Messages are applied
    to the current selected room only — cross-room chatter is filtered out.
    """
    while not stop_event.is_set():
        token = _mint_sse_token(relay, secret, recipient)
        if not token:
            # Either auth failed (give the poll loop the 'unreachable' label)
            # or the relay is down. Wait and retry.
            stop_event.wait(SSE_RECONNECT_S)
            continue

        url = f"{relay}/stream/{recipient}?token={token}"
        try:
            with httpx.stream("GET", url, timeout=None) as resp:
                if resp.status_code != 200:
                    stop_event.wait(SSE_RECONNECT_S)
                    continue
                state.set_connected(True, "Live")
                event_name = ""
                data_buf: list[str] = []
                for line in resp.iter_lines():
                    if stop_event.is_set():
                        return
                    if not line:
                        # Event boundary — dispatch the buffered event
                        if event_name == "message" and data_buf:
                            raw = "\n".join(data_buf)
                            try:
                                msg = json.loads(raw)
                            except (json.JSONDecodeError, ValueError):
                                msg = None
                            if isinstance(msg, dict):
                                msg_room = msg.get("room", "")
                                if msg_room and msg_room == state.selected_room_name():
                                    state.append_message(msg)
                        event_name = ""
                        data_buf = []
                        continue
                    if line.startswith(":"):
                        continue  # keepalive comment
                    if line.startswith("event:"):
                        event_name = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_buf.append(line[len("data:"):].lstrip())
        except Exception:
            # Stream dropped — reconnect after a backoff
            pass

        stop_event.wait(SSE_RECONNECT_S)


# ── Renderers ─────────────────────────────────────────────────────────────────

def _render_header(relay_url: str, agent_name: str, connected: bool, status: str) -> Panel:
    dot_style = "bold #10b981" if connected else "bold #ef4444"
    # Strip protocol for display brevity
    display_relay = relay_url.replace("http://", "").replace("https://", "")
    header_text = Text()
    header_text.append("  quorus", style="bold #14b8a6")
    header_text.append("  ·  ", style="#475569")
    # Agent name always in purple (#a78bfa) with @ prefix — matches ui.fmt_agent()
    header_text.append(f"@{agent_name}", style="bold #a78bfa")
    header_text.append("  ·  ", style="#475569")
    header_text.append(display_relay, style="#64748b")
    header_text.append("   ", style="#64748b")
    # Always ⏵ — color conveys state (green when connected, red when offline)
    header_text.append("⏵", style=dot_style)
    header_text.append(f" {status}" if status else "", style="#64748b")
    return Panel(header_text, border_style="#14b8a6", padding=(0, 1))


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

        is_selected = i == selected_idx
        if is_selected:
            t.append(" ❯ ", style="bold #14b8a6")
            t.append(f"#{name}", style="bold #fbbf24")
            t.append(f"  {member_count} agents", style="#64748b")
        else:
            t.append("   ", style="")
            t.append(f"#{name}", style="#fbbf24")  # dim amber, always
            t.append(f"  {member_count} agents", style="#475569")
        t.append("\n")

    return Panel(
        t,
        title="[bold #14b8a6]Rooms[/]",
        border_style="#1e293b",
        padding=(0, 1),
        subtitle="[#475569]↑↓ switch · n new[/]",
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

        lines.append(f"[{ts}] ", style="#475569")

        is_me = sender == my_name
        # @sender in purple for you, sender's accent for others — always @-prefixed
        sender_color = "#a78bfa" if is_me else _sender_color(sender)
        lines.append(f"@{sender}", style=f"bold {sender_color}")

        if mtype not in ("chat", ""):
            lines.append(f" [{mtype}]", style="#64748b italic")

        lines.append(": ", style="#64748b")
        # Message body: bright for you, regular for others — no bright_white/grey15
        lines.append(content + "\n", style="#f0ede8" if is_me else "#cbd5e1")

    return Panel(
        lines,
        title=f"[bold]#{room_name}[/bold]",
        border_style="#14b8a6",
        padding=(0, 1),
        subtitle="[dim]Enter send  Ctrl+C quit[/dim]",
    )


def _render_input_bar(agent_name: str, status_msg: str) -> Panel:
    content = Text()
    content.append("❯ ", style="bold #14b8a6")
    if status_msg:
        # Color by message type: errors red, success green, else amber (info)
        lower = status_msg.lower()
        if any(w in lower for w in ("error", "failed", "couldn't", "can't")):
            style = "#ef4444"
        elif any(w in lower for w in ("joined", "created", "sent", "ok")):
            style = "#10b981"
        else:
            style = "#f59e0b italic"
        content.append(status_msg, style=style)
    else:
        content.append(
            "type a message · /help for commands · ctrl+c to quit",
            style="#475569",
        )
    return Panel(content, border_style="#14b8a6", padding=(0, 1))


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

def run_hub() -> None:
    console = Console()

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
            if msgs is not None:
                state.set_messages(msgs)
    else:
        state.set_connected(False, "No rooms yet")

    # 4. Start SSE push listener (primary) + slow reconciliation poller (fallback).
    # SSE delivers messages in <100ms; poll is a 10s safety net for dropped streams.
    stop_event = threading.Event()
    poller = threading.Thread(
        target=_poll_loop,
        args=(relay_url, secret, state, stop_event),
        daemon=True,
    )
    poller.start()
    sse_listener = threading.Thread(
        target=_sse_loop,
        args=(relay_url, secret, agent_name, state, stop_event),
        daemon=True,
    )
    sse_listener.start()

    # 5. Main interactive loop
    # Rich Live doesn't support input() — we use clear+redraw + input() pattern
    # (same approach as quorus_tui.py, which proven to work)
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
                # Guard against None-selected (no rooms yet) — don't let the
                # TUI crash on first launch before any room exists.
                if selected is None:
                    room_name = "general"
                else:
                    room_name = selected.get("name") or selected.get("id") or "general"
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
                line = input("❯ ").strip()
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
                        _load_history_into(state, relay_url, secret, rname)
                last_render = 0
                continue

            if cmd == "\x1b[B" or cmd_lower == "down":
                state.select_next()
                selected = state.get_selected_room()
                if selected:
                    rname = selected.get("name") or selected.get("id", "")
                    if rname:
                        _join_room(relay_url, secret, rname, agent_name)
                        _load_history_into(state, relay_url, secret, rname)
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
                    _load_history_into(state, relay_url, secret, match["name"])
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
                        _load_history_into(state, relay_url, secret, new_name)
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
                        _load_history_into(state, relay_url, secret, new_name_raw)
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
                    token = "quorus_join_" + _b64.urlsafe_b64encode(payload.encode()).decode()
                    # Print above the TUI frame and block until the user confirms —
                    # otherwise the next loop iteration redraws the frame and the
                    # token scrolls off-screen before they can copy it.
                    console.print()
                    console.print(f"  [bold]Invite '{invitee}' to #{rname}[/bold]")
                    console.print("  [dim]Share this token:[/dim]")
                    console.print(f"  [#14b8a6]{token}[/#14b8a6]")
                    console.print(f"  [dim]They run: quorus join {token} --name {invitee}[/dim]")
                    console.print()
                    try:
                        input("  [Press Enter to return to the hub] ")
                    except EOFError:
                        pass  # stdin closed — just continue; don't hang
                    # KeyboardInterrupt intentionally propagates so Ctrl-C at
                    # the invite prompt exits the hub (same UX as the main
                    # input loop), caught by the outer try/except.
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

            sent_id = _send_message(relay_url, secret, room_name, agent_name, cmd)
            if sent_id is not None:
                state.set_status_bar("")
                # Optimistic local echo — render immediately, don't wait for SSE
                # round-trip. The server-assigned ID pre-arms the dedup set so
                # when SSE (or the reconciliation poll) redelivers the same
                # message, it won't render twice.
                echo = {
                    "from_name": agent_name,
                    "content": cmd,
                    "message_type": "chat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "room": room_name,
                }
                if sent_id:
                    # Store both keys so dedup matches whether SSE pushes the
                    # fan-out form (message_id) or history returns the canonical
                    # form (id).
                    echo["id"] = sent_id
                    echo["message_id"] = sent_id
                state.append_message(echo)
            else:
                state.set_status_bar(
                    "Couldn't reach the relay. Is it running? "
                    "Try: RELAY_SECRET=x python -m quorus.relay"
                )

            last_render = 0  # force immediate redraw after send

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        console.print("\n[dim]Quorus Hub closed. Goodbye.[/dim]")
