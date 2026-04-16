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
from typing import Optional

try:
    import httpx
except ImportError:
    print("httpx not installed — pip install httpx")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.prompt import Prompt
    from rich.text import Text
except ImportError:
    print("rich not installed — pip install rich")
    sys.exit(1)

# Use the shared theme-aware console so `[primary]`, `[agent]`, etc. resolve
# consistently with every ui.py-rendered command. Avoid constructing a raw
# `Console()` elsewhere in this module — hardcoded hex drifts from the theme
# as soon as ui.py palette changes.
from quorus_cli import ui as _ui  # noqa: E402

from quorus.config import ConfigManager, resolve_config_dir

# ── Constants ──────────────────────────────────────────────────────────────────
# Render cadence — how fast the main loop redraws after input/state change.
# Keep small so the panel view feels live.
RENDER_TICK_S = 2
# History reconciliation poll — slow fallback that catches up missed SSE
# pushes (e.g. after a dropped stream). Bounded by server rate limits.
HISTORY_POLL_S = 10
# Legacy name kept for the render-cadence check below.
POLL_S = RENDER_TICK_S
MAX_MSG = 40
SSE_RECONNECT_S = 2  # backoff between dropped-stream reconnects

CONFIG_DIR = resolve_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_RELAY = "http://localhost:8080"
# Fallback public relay for fresh-user signup. `quorus.dev` is the
# marketing site (Vercel) — the actual relay is hosted on Fly at
# `quorus-relay.fly.dev`. Update this if/when the relay gets its own
# subdomain (e.g. `api.quorus.dev`).
PUBLIC_RELAY = "https://quorus-relay.fly.dev"

# Sender palette — the 3 semantic colors from ui.py, nothing else.
# Names hash deterministically so the same sender is always the same
# color across runs. `agent` (purple) is reserved for the viewer's own
# messages, so senders cycle over the remaining two.
_SENDER_COLORS = ("primary", "room", "accent")


def _sender_color(name: str) -> str:
    """Deterministic sender → theme-token color. Same input always
    yields the same output — no global cycling state, no collisions
    across restarts. Uses a stable hash (not Python's PYTHONHASHSEED-
    salted built-in) so `@arav` is the same color for everyone."""
    import hashlib as _h

    digest = _h.blake2s(name.encode("utf-8"), digest_size=2).digest()
    bucket = int.from_bytes(digest, "big") % len(_SENDER_COLORS)
    return _SENDER_COLORS[bucket]


# ── Config ────────────────────────────────────────────────────────────────────

def _save_instance_config(
    name: str,
    relay_url: str,
    secret: str = "",
    api_key: str = "",
) -> None:
    """Persist the TUI's instance config through the shared ConfigManager."""
    ConfigManager(CONFIG_FILE).save(
        {
            "relay_url": relay_url.rstrip("/"),
            "instance_name": name,
            "relay_secret": secret,
            "api_key": api_key,
            "poll_mode": "sse",
            "push_notification_method": "notifications/claude/channel",
            "push_notification_channel": "quorus",
        }
    )


def _signup(relay_url: str, name: str, workspace: str) -> dict | None:
    """Call the self-service signup endpoint. Returns response dict or None on error.

    The relay only returns the raw API key when the caller identifies as a
    local-setup client via ``X-Quorus-Setup-Local: 1``. Without that header the
    server responds with ``api_key: None`` for security, which used to leave
    the TUI with no auth material after signup. See
    ``quorus/auth/routes.py`` signup handler for the contract.
    """
    try:
        r = httpx.post(
            f"{relay_url.rstrip('/')}/v1/auth/signup",
            json={"name": name, "workspace": workspace},
            headers={"X-Quorus-Setup-Local": "1"},
            timeout=10,
            follow_redirects=True,
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


def _try_connect(url: str, timeout: float = 10) -> bool:
    """Test if a relay is reachable.

    Follows redirects (Fly.io forces http→https; subdomains proxy).
    Timeout is generous (10s) and we do one silent retry — Fly machines
    scale-to-zero, so a cold boot can take a few seconds to respond
    even though the relay is fine. A 3s timeout here caused the TUI
    to falsely report "not reachable" during onboarding.
    """
    def _probe() -> bool:
        try:
            r = httpx.get(
                f"{url.rstrip('/')}/health",
                timeout=timeout,
                follow_redirects=True,
            )
            return r.status_code == 200
        except Exception:
            return False

    if _probe():
        return True
    # Retry once — covers cold-start wake-ups on Fly + brief blips.
    return _probe()


def _prompt_name(console: Console) -> str:
    """Collect a valid participant name. One prompt, tight validation.

    Intentionally terse — no preamble line, no flourish. The wizard
    above already framed the question.
    """
    while True:
        name = Prompt.ask("  Your name").strip()
        if not name:
            console.print("  [dim]Empty — try again.[/]")
            continue
        if len(name) > 64:
            console.print("  [dim]Keep it under 64 characters.[/]")
            continue
        if not re.match(r"^[A-Za-z0-9_\-]+$", name):
            console.print(
                "  [dim]Letters, numbers, hyphens, underscores only.[/]"
            )
            continue
        return name


def _decode_invite_token(token: str) -> dict | None:
    """Decode a pasted `quorus://` or `quorus_join_` invite token.

    Returns {relay_url, secret, api_key, room} on success, None if the
    format isn't recognized. Accepts both modern URI form and legacy
    `quorus_join_` / `murm_join_` base64 payloads.
    """
    import base64 as _b64
    import json as _json

    raw = token.strip()
    try:
        if raw.startswith("quorus://"):
            payload = _json.loads(
                _b64.urlsafe_b64decode(raw[len("quorus://"):].encode()).decode()
            )
            return {
                "relay_url": (payload.get("r", "") or "").rstrip("/"),
                "secret": payload.get("s", "") or "",
                "api_key": payload.get("k", "") or "",
                "room": payload.get("n", "") or "",
            }
        if raw.startswith("quorus_join_") or raw.startswith("murm_join_"):
            prefix = (
                "quorus_join_" if raw.startswith("quorus_join_") else "murm_join_"
            )
            payload = _json.loads(
                _b64.urlsafe_b64decode(raw[len(prefix):].encode()).decode()
            )
            return {
                "relay_url": (payload.get("relay_url", "") or "").rstrip("/"),
                "secret": payload.get("secret", "") or "",
                "api_key": "",
                "room": payload.get("room", "") or "",
            }
    except Exception:
        return None
    return None


def _run_signup(
    console: Console, relay_url: str, name: str, workspace: str,
) -> tuple[str, str]:
    """Run the signup flow on `relay_url`. Returns (api_key, workspace) on
    success, ("", "") on failure. Handles rate-limit / workspace-taken /
    missing-key cases inline with user-friendly messages."""
    console.print("  [dim]Creating your account...[/] ", end="")
    result = _signup(relay_url, name, workspace)

    if result is None:
        console.print("[red]failed[/]")
        console.print("  [dim]Signup endpoint unreachable.[/]")
        return "", ""
    if "error" in result:
        console.print("[red]failed[/]")
        err = result["error"]
        if err == "workspace_taken":
            console.print(
                f"  [dim]Workspace '[bold]{workspace}[/]' already taken.[/]"
            )
            # Retry with a random-ish suffix, user can rename later.
            import secrets as _s
            alt = f"{workspace}-{_s.token_hex(2)}"
            console.print(f"  [dim]Retrying with [bold]{alt}[/]...[/] ", end="")
            result = _signup(relay_url, name, alt)
            if result and "error" not in result and result.get("api_key"):
                console.print("[bold green]✓[/]")
                return result["api_key"], alt
            console.print("[red]still failed[/]")
            return "", ""
        if err == "rate_limited":
            console.print("  [dim]Rate limited — try again in an hour.[/]")
        else:
            console.print(f"  [dim]{err}[/]")
        return "", ""

    api_key = result.get("api_key") or ""
    if not api_key:
        console.print("[red]no key returned[/]")
        console.print(
            "  [dim]Server accepted signup but didn't return a key. "
            "Run [bold]quorus doctor[/] to diagnose.[/]"
        )
        return "", ""

    console.print("[bold green]✓[/]")
    return api_key, workspace


def _first_launch_setup(console: Console) -> dict:
    """Interactive first-launch wizard. Returns config dict.

    Single-screen onboarding. Cadence:

        (banner)
        Welcome to Quorus. One group chat for you and every agent.

        Your name > arav

        Looking for a relay... ✓ local relay at localhost:8080
        You're in as @arav.

    Fast-path requires exactly one prompt. No-local-relay path adds one
    more ("Token or Enter to sign up"). No other prompts, ever.
    """
    from quorus_cli import ui as _ui

    try:
        _ui.banner()
    except Exception:
        console.print(
            "\n  [bold primary]quorus[/]  "
            "[dim]coordination for agent swarms[/]\n"
        )

    console.print(
        "  [muted]Welcome to Quorus. "
        "One group chat for you and every agent.[/]\n"
    )

    name = _prompt_name(console)

    relay_url = DEFAULT_RELAY
    secret = ""
    api_key = ""
    relay_ok = False

    # Happy path: auto-detect local relay.
    console.print()
    console.print("  [dim]Looking for a relay...[/] ", end="")
    if _try_connect(DEFAULT_RELAY):
        console.print(f"[success]✓[/] [muted]local relay at {DEFAULT_RELAY}[/]")
        relay_ok = True
    else:
        console.print("[muted]no local relay found[/]")
        console.print()
        console.print(
            "  [muted]Paste a[/] [accent]quorus://[/] [muted]or[/] "
            "[accent]ABCD-EFGH[/] [muted]invite code, or press Enter to "
            "sign up on[/] [primary]quorus-relay.fly.dev[/][muted].[/]"
        )
        raw = Prompt.ask("\n  Invite or Enter", default="").strip()

        decoded = _decode_invite_token(raw) if raw else None
        if raw and decoded:
            relay_url = decoded["relay_url"]
            secret = decoded["secret"]
            api_key = decoded["api_key"]
            console.print(f"\n  [dim]Connecting to {relay_url}...[/] ", end="")
            if _try_connect(relay_url):
                console.print("[success]✓[/]")
                relay_ok = True
                if decoded.get("room"):
                    console.print(
                        f"  [muted]You'll land in [room]#{decoded['room']}[/] "
                        "automatically.[/]"
                    )
            else:
                console.print("[warning]not reachable[/]")
        elif raw:
            # Try short code — server-side resolve covers the cases where
            # the input isn't a quorus:// URI. This closes the loop for
            # teammates whose share output gave them a code.
            from quorus.services.join_code_svc import normalize_code

            canonical = normalize_code(raw)
            if canonical is None:
                console.print(
                    "\n  [error]Couldn't recognize that input.[/] "
                    "[dim]Expected a quorus:// token or a code like "
                    "ABCD-EFGH.[/]"
                )
            else:
                console.print(
                    f"\n  [dim]Resolving [accent]{raw}[/] against "
                    f"[primary]{PUBLIC_RELAY}[/]...[/] ", end="",
                )
                try:
                    resp = httpx.get(
                        f"{PUBLIC_RELAY}/v1/join/resolve/{canonical}",
                        timeout=10,
                        follow_redirects=True,
                    )
                except Exception:
                    resp = None
                if resp is not None and resp.status_code == 200:
                    payload = (resp.json() or {}).get("payload") or {}
                    relay_url = (payload.get("r") or "").rstrip("/")
                    secret = payload.get("s", "") or ""
                    api_key = payload.get("k", "") or ""
                    console.print("[success]✓[/]")
                    relay_ok = bool(relay_url)
                    if payload.get("n"):
                        console.print(
                            f"  [muted]You'll land in [room]#{payload['n']}[/] "
                            "automatically.[/]"
                        )
                else:
                    console.print("[error]code not found or expired[/]")
        else:
            # Empty input → signup against the public relay.
            relay_url = PUBLIC_RELAY
            console.print(f"\n  [dim]Signing you up on {relay_url}...[/]")
            if not _try_connect(relay_url):
                console.print(
                    "  [error]Couldn't reach the relay.[/] "
                    "[dim]Check your internet and rerun `quorus`.[/]"
                )
            else:
                default_workspace = re.sub(r"[^a-z0-9\-]", "-", name.lower())[:32]
                if not default_workspace or default_workspace[0] == "-":
                    default_workspace = f"ws-{default_workspace}".strip("-")
                api_key, workspace = _run_signup(
                    console, relay_url, name, default_workspace,
                )
                if api_key:
                    relay_ok = True
                    console.print(
                        f"  [muted]Workspace [bold]{workspace}[/] — "
                        "config saved to ~/.quorus/config.json[/]"
                    )

    # If still not connected, guide without pretending we succeeded.
    if not relay_ok and relay_url == DEFAULT_RELAY:
        console.print()
        console.print(
            "  [dim]Start a local relay in another terminal:[/] "
            "[accent]quorus relay[/]"
        )
        console.print(
            "  [dim]Then rerun [bold]quorus[/] — config already saved.[/]"
        )

    # Save config — even partial configs so the next run starts warm.
    _save_instance_config(name, relay_url, secret=secret, api_key=api_key)

    if relay_ok:
        console.print(f"\n  [success]✓[/] [muted]You're in as[/] [agent]@{name}[/].")
        time.sleep(0.15)

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
            follow_redirects=True,
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
        r = httpx.get(
            f"{relay}/rooms",
            headers=_auth_headers(secret),
            timeout=5,
            follow_redirects=True,
        )
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
            follow_redirects=True,
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
            follow_redirects=True,
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
            follow_redirects=True,
        )
    except Exception:
        pass


def _create_room(
    relay: str, secret: str, name: str, creator: str,
) -> tuple[str, Optional[dict]]:
    """Create a room. Returns (status, room_data_or_none).

    Status is one of: 'ok' (created), 'conflict' (409 name taken),
    'auth' (401/403), 'unreachable' (network error), 'http:<code>'
    (other). The caller can branch on this instead of guessing from
    a bool.
    """
    try:
        r = httpx.post(
            f"{relay}/rooms",
            headers=_auth_headers(secret),
            json={"name": name, "created_by": creator},
            timeout=5,
            follow_redirects=True,
        )
        if r.status_code in (200, 201):
            return ("ok", r.json())
        if r.status_code == 409:
            return ("conflict", None)
        if r.status_code in (401, 403):
            return ("auth", None)
        return (f"http:{r.status_code}", None)
    except Exception:
        return ("unreachable", None)


def _create_error_msg(room: str, status: str, relay: str) -> str:
    """Turn a _create_room status code into a user-facing status-bar line."""
    if status == "conflict":
        return f"Room '{room}' already exists — pick a different name."
    if status == "auth":
        return "Not authenticated — run: quorus doctor"
    if status == "unreachable":
        return f"Can't reach relay at {relay} — is it running?"
    return f"Couldn't create '{room}' — {status}"


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
        self.help_visible: bool = False
        # True once a separator rule has been drawn above an inline SSE
        # message in the current typing session. Reset when the main loop
        # returns from input() so the next typing session starts fresh.
        self._inline_rule_drawn: bool = False
        # Per-room unread count. Incremented when an SSE message lands
        # for a non-selected room; zeroed when the user selects it.
        self.unread: dict[str, int] = {}
        # Last observed relay round-trip in milliseconds. None = unknown.
        self.latency_ms: int | None = None

    def increment_unread(self, room_name: str) -> None:
        with self._lock:
            self.unread[room_name] = self.unread.get(room_name, 0) + 1

    def clear_unread(self, room_name: str) -> None:
        with self._lock:
            self.unread.pop(room_name, None)

    def get_unread(self, room_name: str) -> int:
        with self._lock:
            return self.unread.get(room_name, 0)

    def total_unread(self) -> int:
        with self._lock:
            return sum(self.unread.values())

    def set_latency_ms(self, ms: int | None) -> None:
        with self._lock:
            self.latency_ms = ms

    def get_latency_ms(self) -> int | None:
        with self._lock:
            return self.latency_ms

    def toggle_help(self) -> None:
        with self._lock:
            self.help_visible = not self.help_visible

    def is_help_visible(self) -> bool:
        with self._lock:
            return self.help_visible

    def mark_inline_rule_if_fresh(self) -> bool:
        """Claim the 'first inline message' slot. Returns True iff this call
        flipped the flag from False → True (so the caller should draw the
        rule exactly once per typing session)."""
        with self._lock:
            if self._inline_rule_drawn:
                return False
            self._inline_rule_drawn = True
            return True

    def reset_inline_rule(self) -> None:
        """Call after input() returns so the next incoming SSE message
        will get a fresh separator rule."""
        with self._lock:
            self._inline_rule_drawn = False

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

    def _selected_name_locked(self) -> str:
        """Current room's name. Must be called inside the lock."""
        if not self.rooms:
            return ""
        idx = max(0, min(self.selected_room_idx, len(self.rooms) - 1))
        r = self.rooms[idx]
        return r.get("name") or r.get("id") or ""

    def select_next(self) -> None:
        with self._lock:
            if self.rooms:
                self.selected_room_idx = (self.selected_room_idx + 1) % len(self.rooms)
                # Entering a room counts as reading it.
                self.unread.pop(self._selected_name_locked(), None)

    def select_prev(self) -> None:
        with self._lock:
            if self.rooms:
                self.selected_room_idx = (self.selected_room_idx - 1) % len(self.rooms)
                self.unread.pop(self._selected_name_locked(), None)

    def select_by_name(self, name: str) -> None:
        with self._lock:
            for i, r in enumerate(self.rooms):
                if r.get("name") == name:
                    self.selected_room_idx = i
                    self.unread.pop(name, None)
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
    exists only to recover from dropped streams and reconcile missed messages.

    Opportunistically measures the /health round-trip each cycle so the
    header can show a live latency number.
    """
    import time as _time

    while not stop_event.is_set():
        try:
            # Measure latency in a lightweight /health probe that runs
            # regardless of whether rooms succeed — keeps the number
            # flowing even when the tenant has no rooms yet.
            t0 = _time.perf_counter()
            try:
                hr = httpx.get(
                    f"{relay.rstrip('/')}/health",
                    timeout=5,
                    follow_redirects=True,
                )
                if hr.status_code == 200:
                    state.set_latency_ms(
                        int((_time.perf_counter() - t0) * 1000)
                    )
                else:
                    state.set_latency_ms(None)
            except Exception:
                state.set_latency_ms(None)

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

        stop_event.wait(HISTORY_POLL_S)


# ── Real-time push (SSE) ──────────────────────────────────────────────────────

def _mint_sse_token(relay: str, secret: str, recipient: str) -> Optional[str]:
    """Exchange bearer auth for a short-lived SSE stream token."""
    try:
        r = httpx.post(
            f"{relay}/stream/token",
            headers=_auth_headers(secret),
            json={"recipient": recipient},
            timeout=5,
            follow_redirects=True,
        )
        if r.status_code == 200:
            return r.json().get("token")
    except Exception:
        pass
    return None


def _print_inline_message(
    console: Console, msg: dict, my_name: str, state: "HubState"
) -> None:
    """Print one chat line directly to stdout.

    The main render loop is blocked on input() — so panel redraws only
    happen after Enter. To make incoming messages feel instant, the SSE
    thread prints them here as they arrive. The next full redraw still
    shows them in the chat pane too (dedup prevents doubles).

    The first inline message in a typing session is preceded by a subtle
    dotted rule so the user sees "messages below this line arrived while
    you were typing". Rule is reset on Enter (cleared via mark_render).
    """
    from rich.rule import Rule

    sender = msg.get("from_name", "?")
    content = msg.get("content", "")
    ts_raw = msg.get("timestamp", "")
    try:
        hhmm = ts_raw[11:16] if len(ts_raw) >= 16 else ""
    except Exception:
        hhmm = ""
    # Skip our own echoed messages — we already showed the optimistic echo.
    if sender == my_name:
        return
    sender_style = _sender_color(sender)

    # Draw the separator rule once per "typing session" so multiple incoming
    # messages don't get multiple rules stacked on top of each other.
    if state.mark_inline_rule_if_fresh():
        console.print(Rule(style="dim", characters="·"))

    line = Text()
    if hhmm:
        line.append(f"  [{hhmm}] ", style="muted")
    else:
        line.append("  ", style="muted")
    line.append(f"@{sender}", style=f"bold {sender_style}")
    line.append(": ", style="dim")
    line.append(content, style="white")
    console.print(line)


def _sse_loop(
    relay: str,
    secret: str,
    recipient: str,
    state: HubState,
    console: Console,
    stop_event: threading.Event,
) -> None:
    """Subscribe to SSE push and apply messages as they arrive.

    Runs in a daemon thread. On disconnect (network blip, relay restart,
    token expiry) it reconnects with a short backoff. Messages are applied
    to the current selected room only — cross-room chatter is filtered out.
    Messages also print inline so the user sees them without waiting for
    the main loop to redraw.
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
                                    # Active room — append + inline print.
                                    key = HubState._dedup_key(msg)
                                    is_new = not key or key not in state._seen_set
                                    state.append_message(msg)
                                    if is_new:
                                        _print_inline_message(
                                            console, msg, recipient, state,
                                        )
                                elif msg_room and msg.get("from_name") != recipient:
                                    # Background room — bump the unread
                                    # badge so the header + room strip
                                    # reflect the activity next redraw.
                                    state.increment_unread(msg_room)
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
# Flat, borderless layout. No Panels. Color + indentation carry the
# hierarchy. Each renderer returns a `ConsoleRenderable` that the main
# loop prints directly.


# ── Message-type glyphs ───────────────────────────────────────────────────────
# One-char prefix that replaces the old `[message_type]` bracket tag.
# Reserved glyphs are chosen to be visually distinct at small sizes.
_MSG_TYPE_GLYPH = {
    "chat": "",
    "system": "·",
    "brief": "›",
    "decision": "◆",
    "task": "›",
    "note": "·",
}


def _render_header(
    relay_url: str,
    agent_name: str,
    connected: bool,
    status: str,
    *,
    room_count: int = 0,
    unread_total: int = 0,
    latency_ms: int | None = None,
) -> Text:
    """Premium one-line header.

    Format: `  @arav · 3 rooms · 12 unread     host  ⏵ 42ms`

    The literal "quorus" word is dropped — the brand lives in the
    banner + first-run wizard. Header space now carries live signal:
    identity, room count, unread badge, host, connection + latency.
    """
    from rich.text import Text as _Text

    display_relay = (
        relay_url.replace("http://", "").replace("https://", "")
    )
    dot_style = "bold success" if connected else "bold error"

    header = _Text()
    header.append("  ")
    header.append(f"@{agent_name}", style="bold agent")

    if room_count:
        header.append("  ·  ", style="dim")
        header.append(
            f"{room_count} {'room' if room_count == 1 else 'rooms'}",
            style="muted",
        )
    if unread_total:
        header.append("  ·  ", style="dim")
        header.append(
            f"{unread_total} unread", style="bold room",
        )

    # Right side: host + connection pulse + latency (when known).
    header.append("     ", style="muted")
    header.append(display_relay, style="muted")
    header.append("  ", style="muted")
    header.append("⏵", style=dot_style)
    if latency_ms is not None:
        header.append(f" {latency_ms}ms", style="muted")
    elif status:
        header.append(f" {status}", style="muted")
    return header


def _render_room_strip(
    rooms: list[dict],
    selected_idx: int,
    *,
    unread_by_room: dict[str, int] | None = None,
) -> Text:
    """Inline room strip with unread dots.

    `#dev•3   #design   #ops•1` — active room bold amber, others dim,
    unread count rendered inline as `•N` without a border.
    """
    unread_by_room = unread_by_room or {}

    if not rooms:
        return Text.from_markup(
            "  [dim]· no rooms yet · press[/] [bold]n[/] "
            "[dim]to create one ·[/]"
        )

    strip = Text("  ")
    for i, room in enumerate(rooms):
        name = room.get("name") or room.get("id", "?")
        is_selected = i == selected_idx
        unread = unread_by_room.get(name, 0)
        if is_selected:
            strip.append(f"#{name}", style="bold room")
        else:
            strip.append(f"#{name}", style="dim")
        if unread and not is_selected:
            strip.append(f"•{unread}", style="bold room")
        if i < len(rooms) - 1:
            strip.append("   ·   ", style="dim")
    return strip


def _format_header_line(
    msg: dict, my_name: str, *, glyph: str = "",
) -> Text:
    """Group-leader line: `  · [HH:MM]  @sender  body`.

    Used for the first message in a sender-group (and any message
    after a >120s gap). Continuation messages skip the leader and
    just indent.
    """
    sender = msg.get("from_name") or msg.get("sender", "?")
    content = msg.get("content", "")
    ts_raw = msg.get("timestamp", "")

    try:
        hhmm = ts_raw[11:16] if len(ts_raw) >= 16 else ts_raw[:5]
    except Exception:
        hhmm = "??:??"

    is_me = sender == my_name
    sender_color = "agent" if is_me else _sender_color(sender)

    line = Text()
    line.append("  ")
    # Glyph column (1 char) for message-type differentiation.
    if glyph:
        line.append(glyph, style="muted")
        line.append(" ")
    else:
        line.append("  ", style="")
    line.append(f"[{hhmm}]", style="dim")
    line.append("  ")
    line.append(f"@{sender}", style=f"bold {sender_color}")
    line.append("  ")
    line.append(content, style="bright_white" if is_me else "muted")
    return line


def _format_continuation_line(msg: dict, my_name: str) -> Text:
    """Continuation line — same sender, within 120s of the previous.

    Indented to align under the previous message's body so the eye
    reads each sender-group as a block (iMessage rhythm).
    """
    sender = msg.get("from_name") or msg.get("sender", "?")
    content = msg.get("content", "")
    is_me = sender == my_name
    # Indent matches the `  · [HH:MM]  @sender  ` column — roughly
    # 2 + 2 + 7 + 2 + len(@sender) + 2, but we fix at 20 so names of
    # varying length all align cleanly.
    indent = " " * 20
    line = Text(indent)
    line.append(content, style="bright_white" if is_me else "muted")
    return line


def _ts_epoch(msg: dict) -> float:
    """Parse a message's ISO timestamp to epoch seconds. 0 on failure."""
    from datetime import datetime

    ts_raw = msg.get("timestamp", "")
    if not ts_raw:
        return 0.0
    try:
        # Handle trailing Z and explicit offset.
        normalized = ts_raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _empty_card(
    console_width: int,
    *,
    glyph: str,
    title: str,
    cta: str,
) -> list[Text]:
    """A vertically-centered 5-line empty state.

    No borders, no panel — just centered Text lines. Used when there
    are no rooms OR no messages in the selected room.
    """

    def _center(s: str, style: str) -> Text:
        pad = max(0, (console_width - len(s)) // 2)
        return Text(" " * pad + s, style=style)

    return [
        Text(""),
        _center(glyph, "bold primary"),
        Text(""),
        _center(title, "muted"),
        Text(""),
        _center(cta, "dim"),
    ]


def _render_chat_feed(
    messages: list[dict],
    room_name: str,
    my_name: str,
    *,
    console_width: int = 80,
) -> list[Text]:
    """Flat message feed with sender-grouping + empty cards.

    - Sender-group: consecutive messages from the same sender within
      120s collapse. Only the first shows `[HH:MM] @sender`; later
      lines are indented to the body column.
    - Empty states get a centered 5-line card.
    """
    if not room_name:
        return _empty_card(
            console_width,
            glyph="⌘",
            title="no room yet",
            cta="press  n  or type  /create <name>",
        )
    if not messages:
        return _empty_card(
            console_width,
            glyph="◌",
            title=f"#{room_name} is quiet",
            cta="type a message to get started",
        )

    lines: list[Text] = []
    prev_sender: str | None = None
    prev_ts: float = 0.0
    for msg in messages:
        sender = msg.get("from_name") or msg.get("sender", "?")
        mtype = msg.get("message_type", "chat")
        ts = _ts_epoch(msg)
        same_group = (
            sender == prev_sender
            and prev_ts
            and (ts - prev_ts) < 120
            and mtype == "chat"
        )

        if same_group:
            lines.append(_format_continuation_line(msg, my_name))
        else:
            # Blank line between groups for breathing room.
            if prev_sender is not None:
                lines.append(Text(""))
            glyph = _MSG_TYPE_GLYPH.get(mtype, "")
            lines.append(_format_header_line(msg, my_name, glyph=glyph))

        prev_sender = sender
        prev_ts = ts
    return lines


def _render_status_line(status_msg: str) -> Text | None:
    """Transient status line that appears ABOVE the prompt on the last
    redraw. Returns None if there's nothing to show (so the main loop
    can skip printing a blank line)."""
    if not status_msg:
        return None
    lower = status_msg.lower()
    if any(w in lower for w in ("error", "failed", "couldn't", "can't")):
        style = "error"
    elif any(w in lower for w in ("joined", "created", "sent", "ok")):
        style = "success"
    else:
        style = "muted italic"
    line = Text()
    line.append("  · ", style="dim")
    line.append(status_msg, style=style)
    return line


def _print_help(console: Console) -> None:
    """Print the help overlay — keybinds, slash commands, plain-English forms."""
    console.print()
    console.print("  [bold primary]Keybinds[/]")
    kb_rows = [
        ("↑ / ↓", "switch between rooms"),
        ("Enter", "send the typed message"),
        ("?", "show/hide this help"),
        ("Ctrl+C", "quit the hub"),
    ]
    for kb, desc in kb_rows:
        console.print(
            f"  [bold agent]{kb:<14}[/]  [dim]{desc}[/]"
        )

    console.print()
    console.print("  [bold primary]Slash commands[/]")
    for verb, meta in SLASH_COMMANDS.items():
        console.print(
            f"  [bold primary]{verb:<14}[/]  [dim]{meta[0]}[/]"
        )

    console.print()
    console.print("  [bold primary]Plain English[/]  [dim](aliases for slash commands)[/]")
    rows = [
        ("create <name>",            "create a new room"),
        ("join <room>",              "switch to that room"),
        ("invite <name>",            "show join token for current room"),
        ("quit / exit",              "close the hub"),
        ("<anything else>",          "sent as a chat message to the active room"),
    ]
    for cmd, desc in rows:
        console.print(
            f"  [bold primary]{cmd:<30}[/]  [dim]{desc}[/]"
        )
    console.print()


# ── Slash commands ────────────────────────────────────────────────────────────
# Single source of truth for the slash-command table. Each entry:
#   "/verb": (one-line description, handler)
# Handler signature:
#   handler(arg, state, relay_url, secret, agent_name, console) -> True | "__QUIT__"

def _slash_help(arg, state, relay_url, secret, agent_name, console):
    del arg, relay_url, secret, agent_name
    _print_help(console)
    return True


def _slash_rooms(arg, state, relay_url, secret, agent_name, console):
    del arg, agent_name
    rooms = _fetch_rooms(relay_url, secret)
    if rooms:
        state.set_rooms(rooms)
        state.set_status_bar(f"{len(rooms)} room(s) loaded")
    else:
        state.set_status_bar("no rooms (or relay unreachable)")
    return True


def _slash_join(arg, state, relay_url, secret, agent_name, console):
    del console
    target = arg.strip().lstrip("#")
    if not target:
        state.set_status_bar("usage: /join <room>")
        return True
    rooms_snap = state.get_rooms()
    match = next(
        (r for r in rooms_snap if r.get("name", "").lower() == target.lower()),
        None,
    )
    if not match:
        state.set_status_bar(f"room '{target}' not found — try /rooms")
        return True
    state.select_by_name(match["name"])
    _join_room(relay_url, secret, match["name"], agent_name)
    _load_history_into(state, relay_url, secret, match["name"])
    state.set_status_bar(f"joined #{match['name']}")
    return True


def _slash_create(arg, state, relay_url, secret, agent_name, console):
    del console
    name = arg.strip().lstrip("#")
    if not name or not re.match(r"^[A-Za-z0-9_\-]+$", name):
        state.set_status_bar("usage: /create <name>  (letters/numbers/- only)")
        return True
    status, _room = _create_room(relay_url, secret, name, agent_name)
    if status == "ok":
        state.set_status_bar(f"created #{name}")
        state.set_rooms(_fetch_rooms(relay_url, secret))
        state.select_by_name(name)
        _join_room(relay_url, secret, name, agent_name)
        _load_history_into(state, relay_url, secret, name)
    else:
        state.set_status_bar(_create_error_msg(name, status, relay_url))
    return True


def _slash_invite(arg, state, relay_url, secret, agent_name, console):
    del arg, relay_url, secret, agent_name
    selected = state.get_selected_room()
    if not selected:
        state.set_status_bar("no room selected — use /join <room> first")
        return True
    # The existing "invite <name>" regex path does the real token mint. Here
    # we just nudge the user toward the CLI / existing flow.
    state.set_status_bar(
        f"run: quorus share {selected.get('name','')} — "
        "copies a portable invite token"
    )
    return True


def _slash_status(arg, state, relay_url, secret, agent_name, console):
    del arg, secret, agent_name, console
    connected, label = state.get_connection()
    state.set_status_bar(
        f"{'connected' if connected else 'offline'} · {label} · {relay_url}"
    )
    return True


def _slash_clear(arg, state, relay_url, secret, agent_name, console):
    del arg, relay_url, secret, agent_name, console
    state.set_messages([])
    state.set_status_bar("chat cleared")
    return True


def _slash_quit(arg, state, relay_url, secret, agent_name, console):
    del arg, state, relay_url, secret, agent_name, console
    return "__QUIT__"


SLASH_COMMANDS: dict[str, tuple[str, callable]] = {
    "/help":   ("show keybinds + all commands",        _slash_help),
    "/rooms":  ("refresh the room list",               _slash_rooms),
    "/join":   ("/join <room> — switch rooms",         _slash_join),
    "/create": ("/create <name> — make a new room",    _slash_create),
    "/invite": ("show how to share the current room",  _slash_invite),
    "/status": ("connection + relay info",             _slash_status),
    "/clear":  ("clear the chat pane",                 _slash_clear),
    "/quit":   ("close the hub",                       _slash_quit),
}


def _dispatch_slash(
    verb: str,
    arg: str,
    state: "HubState",
    relay_url: str,
    secret: str,
    agent_name: str,
    console: "Console",
):
    entry = SLASH_COMMANDS.get(verb)
    if entry is None:
        return False
    _desc, handler = entry
    return handler(arg, state, relay_url, secret, agent_name, console)


# ── Main hub loop ─────────────────────────────────────────────────────────────

def run_hub() -> None:
    # Shared theme-aware console (primary/agent/room/muted/dim/success/warning/
    # error/accent all resolve via ui.THEME).
    console = _ui.console

    # 1. Load or create config
    cfg = ConfigManager(CONFIG_FILE).load() or None
    if cfg is None:
        cfg = _first_launch_setup(console)

    relay_url: str = cfg.get("relay_url", DEFAULT_RELAY).rstrip("/")
    agent_name: str = cfg.get("instance_name", "agent")
    raw_secret: str = cfg.get("relay_secret", "") or cfg.get("api_key", "")

    # Exchange API key for JWT if needed
    secret = _get_auth_token(relay_url, raw_secret)

    # 2. Initial room load — auto-create a starter room if this is a blank
    # slate so the user lands in a live chat instead of a dead hub.
    console.print(f"\n  [dim]Connecting to {relay_url}...[/dim]")
    rooms = _fetch_rooms(relay_url, secret)
    if not rooms:
        starter = "general"
        status, _ = _create_room(relay_url, secret, starter, agent_name)
        if status == "ok":
            console.print(
                f"  [dim]Created starter room [room]#{starter}[/] — you can add more anytime.[/]"
            )
            rooms = _fetch_rooms(relay_url, secret)
        elif status == "conflict":
            # Someone else made a room named `general` in the same tenant —
            # silently refetch rather than surface a confusing "exists" error.
            rooms = _fetch_rooms(relay_url, secret)
        elif status == "auth":
            console.print(
                "  [red]Not authenticated.[/] "
                "[dim]Run: quorus doctor[/]"
            )
        elif status == "unreachable":
            console.print(
                f"  [red]Can't reach relay at {relay_url}.[/] "
                "[dim]Is it running? Try: quorus relay[/]"
            )
        # Any other failure → render the empty-state hint from the panel.

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
        args=(relay_url, secret, agent_name, state, console, stop_event),
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
                # Empty string signals "no room selected" to the chat
                # renderer; it uses that to show an honest empty-state
                # instead of lying with a fake "#general" title.
                if selected is None:
                    room_name = ""
                else:
                    room_name = selected.get("name") or selected.get("id") or ""
                msgs_snap = state.get_messages()
                status_bar = state.get_status_bar()

                from rich.rule import Rule as _Rule

                selected_idx: int
                with state._lock:
                    selected_idx = state.selected_room_idx

                # Snapshot live-signal fields for the header.
                unread_total = state.total_unread()
                unread_by_room = {
                    (r.get("name") or r.get("id", "")): state.get_unread(
                        r.get("name") or r.get("id", "")
                    )
                    for r in rooms_snap
                }
                latency_ms = state.get_latency_ms()
                console_width = max(40, console.size.width)

                console.clear()
                # Header row + single hairline rule. No border box.
                console.print(
                    _render_header(
                        relay_url,
                        agent_name,
                        connected,
                        conn_status,
                        room_count=len(rooms_snap),
                        unread_total=unread_total,
                        latency_ms=latency_ms,
                    )
                )
                console.print(_Rule(style="dim"))
                # Inline room strip (horizontal, not a sidebar).
                console.print(
                    _render_room_strip(
                        rooms_snap, selected_idx,
                        unread_by_room=unread_by_room,
                    )
                )
                console.print()
                # Flat chat feed — grouped, centered empty states.
                for feed_line in _render_chat_feed(
                    msgs_snap, room_name, agent_name,
                    console_width=console_width,
                ):
                    console.print(feed_line)
                console.print()
                # First-send hint: only when room is selected and empty.
                if room_name and not msgs_snap and not status_bar:
                    console.print(
                        Text.from_markup(
                            "  [dim]type a message · [/][accent]/help[/] "
                            "[dim]for commands[/]"
                        )
                    )
                # Transient status line (if any) above the bare prompt.
                status_line = _render_status_line(status_bar)
                if status_line is not None:
                    console.print(status_line)
                last_render = now

            # Bare prompt — no panel, no border. Color from the primary
            # theme token so it matches the rest of the TUI.
            try:
                line = input("❯ ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            # User hit Enter — close out the current typing session so the
            # next incoming SSE message draws a fresh separator rule above
            # it (instead of silently stacking).
            state.reset_inline_rule()

            if not line:
                continue

            # ── Plain-English command parsing ──────────────────────────────────
            cmd = line.strip()
            cmd_lower = cmd.lower()

            # quit / exit
            if cmd_lower in ("quit", "exit", "q"):
                break

            # help — plain word or "?" shortcut
            if cmd_lower in ("help", "?"):
                _print_help(console)
                last_render = 0
                continue

            # Slash-command dispatcher (Discord / Slack style). Runs BEFORE the
            # plain-English regex block so the two don't fight over short tokens.
            if cmd.startswith("/"):
                parts = cmd.split(None, 1)
                verb = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""
                handled = _dispatch_slash(
                    verb, arg, state, relay_url, secret, agent_name, console,
                )
                if handled == "__QUIT__":
                    break
                if handled:
                    last_render = 0
                    continue
                state.set_status_bar(
                    f"unknown command: {verb} — try /help"
                )
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
                    status, _room = _create_room(
                        relay_url, secret, new_name, agent_name,
                    )
                    if status == "ok":
                        state.set_status_bar(f"Room '{new_name}' created.")
                        new_rooms = _fetch_rooms(relay_url, secret)
                        state.set_rooms(new_rooms)
                        state.select_by_name(new_name)
                        _join_room(relay_url, secret, new_name, agent_name)
                        _load_history_into(state, relay_url, secret, new_name)
                    else:
                        state.set_status_bar(_create_error_msg(new_name, status, relay_url))
                else:
                    state.set_status_bar("Invalid name (letters, numbers, hyphens only).")
                last_render = 0
                continue

            if create_match:
                new_name_raw = create_match.group(1)
                # Sanitise: strip leading # if present
                new_name_raw = new_name_raw.lstrip("#")
                if re.match(r"^[A-Za-z0-9_\-]+$", new_name_raw):
                    status, _room = _create_room(
                        relay_url, secret, new_name_raw, agent_name,
                    )
                    if status == "ok":
                        state.set_status_bar(f"Room '{new_name_raw}' created.")
                        new_rooms = _fetch_rooms(relay_url, secret)
                        state.set_rooms(new_rooms)
                        state.select_by_name(new_name_raw)
                        _join_room(relay_url, secret, new_name_raw, agent_name)
                        _load_history_into(state, relay_url, secret, new_name_raw)
                    else:
                        state.set_status_bar(
                            _create_error_msg(new_name_raw, status, relay_url)
                        )
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
                    console.print(f"  [primary]{token}[/]")
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
