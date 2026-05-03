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
import os
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

try:
    import readchar
    _READCHAR_AVAILABLE = True
except ImportError:
    _READCHAR_AVAILABLE = False

# Use the shared theme-aware console so `[primary]`, `[agent]`, etc. resolve
# consistently with every ui.py-rendered command. Avoid constructing a raw
# `Console()` elsewhere in this module — hardcoded hex drifts from the theme
# as soon as ui.py palette changes.
from quorus_cli import ui as _ui  # noqa: E402

from quorus.config import ConfigManager, resolve_config_dir

from . import welcome as _welcome

# ── Constants ──────────────────────────────────────────────────────────────────
# Idle redraw cadence — how often the main loop refreshes when nothing has
# happened. SSE message arrival forces an immediate redraw (last_render = 0)
# so this isn't the chat-freshness lever; it's just for latency / unread /
# connection-state cosmetics. 10s is plenty.
#
# Why 10 and not 2: at 2s the TUI fights the user — typing, copy/paste, and
# scrollback all get wiped under them. Bumping to 10s makes the TUI calm
# without making chat feel stale (real messages still redraw instantly).
RENDER_TICK_S = 10

# How long to suppress the idle redraw after the last keystroke. Matches the
# typical multi-line paste / read-and-think interval. The redraw will still
# fire on real events (new SSE message) — this only blocks the cosmetic tick.
INPUT_QUIET_HOLD_S = 5.0


def _full_clear(console) -> None:
    """Clear visible region only; preserve scrollback for copy/paste.

    The previous version emitted `\\x1b[3J` to nuke scrollback every redraw
    so successive frames didn't pile up. The cure was worse than the
    disease: the user couldn't copy any text more than 2 seconds old, and
    couldn't scroll back to anything that had moved off-screen. Now we
    use only `\\x1b[2J\\x1b[H` (visible region + cursor home), which Rich's
    own `console.clear()` also emits — keeping the user's terminal history
    intact. Frame-stacking artifacts in VS Code's xterm.js are mild and
    self-heal on the next full repaint.

    We do NOT use alt-screen mode (`\\x1b[?1049h`) because that disables
    the terminal's native trackpad/mouse scroll.

    Falls back to Rich's clear if stdout isn't a real terminal (e.g. inside
    pytest capture).
    """
    if sys.stdout.isatty():
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
        try:
            console.clear()  # reset Rich's styled state too
        except Exception:
            pass
    else:
        console.clear()

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


# ── Identity disambiguation ───────────────────────────────────────────────
# Heuristic: a sender is an AGENT if any dash-segment of the name contains a
# known harness keyword (codex/claude/gemini/cursor/bot). Otherwise human.
# Keep this list in sync with the suffixes mcp_writers.agent_identity
# generates. "test"/"pm"/"auto" are NOT agent suffixes — they're labels
# humans pick for themselves and we treat them as human.
_AGENT_KEYWORDS = ("codex", "claude", "gemini", "cursor", "bot")


def _name_has_agent_keyword(name: str) -> bool:
    """True iff any dash-segment after the first contains an agent keyword.

    `arav-codex` → True, `arav-claude-1m` → True, `arav-pm` → False,
    `aarya-test` → False. Plain names without dashes are always human.
    """
    if not name or "-" not in name:
        return False
    parts = name.lower().split("-")[1:]
    return any(kw in p for p in parts for kw in _AGENT_KEYWORDS)


def _strip_agent_suffix(name: str) -> str:
    """Return the human prefix of an agent-flavored name.

    Walks forward looking for the first segment containing an agent
    keyword and strips from that point onwards. This correctly handles
    versioned/decorated suffixes like `arav-claude-1m` → `arav` and
    `arav-claude-desktop` → `arav` because the strip happens at the
    first agent-keyword match, not the last keyword-bearing segment.
    """
    if "-" not in name:
        return name
    parts = name.split("-")
    for i, p in enumerate(parts[1:], start=1):
        if any(kw in p.lower() for kw in _AGENT_KEYWORDS):
            return "-".join(parts[:i]) or name
    return name


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


# Module-level "out parameter" used by _send_message to surface the relay's
# rejection reason back to the caller without breaking the Optional[str]
# (sent_id-or-None) return contract that other call sites rely on.
#
# Why a module global instead of returning a tuple: only one caller (the
# main input loop) cares about the error string, and the alternative —
# changing the return type — propagates type churn through every importer
# (tests, mocks, the optimistic-echo path). The send path is single-threaded
# from the user's keyboard, so contention is impossible.
#
# Contract: callers MUST read this immediately after a None return. Any
# subsequent _send_message call clears it. None means "connection error or
# 5xx — show the generic 'couldn't reach the relay' hint".
_LAST_SEND_ERROR: Optional[str] = None


def _send_message(
    relay: str, secret: str, room: str, sender: str, content: str,
) -> Optional[str]:
    """Send a chat message. Returns the server-assigned message ID on success,
    else None. The ID lets the caller dedup its own optimistic echo against
    the real message coming back through SSE / history.

    On a 4xx response, the relay's `detail` field is captured into the
    module-level ``_LAST_SEND_ERROR`` so the caller can surface the actual
    reason ("Cannot send as another user", "Room not found", etc.) instead
    of the misleading generic "Couldn't reach the relay" hint. 5xx and
    connection errors leave ``_LAST_SEND_ERROR`` as None — those genuinely
    are reachability problems and the generic hint is correct.
    """
    global _LAST_SEND_ERROR
    _LAST_SEND_ERROR = None  # reset before each send
    # Retry transient failures (relay restart, brief 5xx blip): 4 total
    # attempts with backoff [0.2, 0.5, 1.0] s — 1.7 s max wait. 4xx is
    # NOT retried (those are real client errors — wrong identity, missing
    # room, etc.) and the existing _LAST_SEND_ERROR plumbing stays intact.
    _RETRY_BACKOFF = (0.2, 0.5, 1.0)
    _RETRY_EXC = (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)
    last_5xx_status: Optional[int] = None
    for attempt in range(len(_RETRY_BACKOFF) + 1):
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
            if 400 <= r.status_code < 500:
                # Relay rejected — surface its reason, not a connection lie.
                try:
                    data = r.json()
                    detail = data.get("detail") if isinstance(data, dict) else None
                    if isinstance(detail, str) and detail:
                        _LAST_SEND_ERROR = detail
                    else:
                        _LAST_SEND_ERROR = f"Relay rejected send (HTTP {r.status_code})"
                except (ValueError, TypeError):
                    _LAST_SEND_ERROR = f"Relay rejected send (HTTP {r.status_code})"
                return None
            # 5xx — retry on next iteration; preserve generic-hint semantics.
            last_5xx_status = r.status_code
        except _RETRY_EXC:
            pass  # transient — retry
        except Exception:
            return None  # bare exception — preserve original fail-closed behavior
        if attempt < len(_RETRY_BACKOFF):
            time.sleep(_RETRY_BACKOFF[attempt])
    _ = last_5xx_status  # silence unused — kept for future logging
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


def _destroy_room(
    relay: str, secret: str, room: str, requested_by: str,
) -> tuple[str, str]:
    """DELETE /rooms/{room}. Returns (status, detail).

    Status: 'ok', 'auth' (401/403), 'missing' (404), 'unreachable',
    or 'http:<code>'. ``detail`` carries server-side error text when present.
    """
    try:
        r = httpx.request(
            "DELETE",
            f"{relay}/rooms/{room}",
            headers={**_auth_headers(secret), "Content-Type": "application/json"},
            content=json.dumps({"requested_by": requested_by}),
            timeout=5,
            follow_redirects=True,
        )
        if r.status_code in (200, 204):
            return ("ok", "")
        if r.status_code in (401, 403):
            return ("auth", _safe_detail(r))
        if r.status_code == 404:
            return ("missing", "")
        return (f"http:{r.status_code}", _safe_detail(r))
    except Exception as exc:
        return ("unreachable", str(exc))


def _safe_detail(r: "httpx.Response") -> str:
    """Pull a human-readable error string from a relay response."""
    try:
        body = r.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("error") or "")
    except Exception:
        pass
    return r.text[:200]


def _mint_join_token(relay_url: str, secret: str, room: str) -> str:
    """Mint a portable join code for *room* via the server-side mint endpoint.

    SECURITY: the prior implementation built a local base64 envelope that
    included the relay secret in plaintext — anyone seeing the resulting
    `quorus_join_…` token gained admin-equivalent auth on the relay. This
    is a hard ship-block (security audit Finding 26, CRITICAL).

    The replacement calls `POST /v1/join/mint` so the relay issues a
    short, single-use code bound to a *scoped invite token* (room-only,
    time-limited). The relay secret never leaves the caller's machine.

    Falls back to the legacy local envelope ONLY when the server returns
    404 (very old relay without /v1/join/mint) — and in that fallback
    the secret is still embedded; we log a warning so operators can
    upgrade. New deployments must always hit the server path.
    """
    try:
        r = httpx.post(
            f"{relay_url.rstrip('/')}/v1/join/mint",
            headers=_auth_headers(secret),
            json={"room": room, "ttl_days": 7},
            timeout=5,
            follow_redirects=True,
        )
        if r.status_code == 200:
            data = r.json()
            code = data.get("code") or data.get("display_code")
            if code:
                return code  # short server-issued code, no secret leak
        if r.status_code == 404:
            # Server too old to support /v1/join/mint — fall through to
            # the legacy envelope and warn at the call site via stderr.
            sys.stderr.write(
                "[quorus] WARN: relay does not support /v1/join/mint — "
                "falling back to legacy base64 envelope. Upgrade the "
                "relay to avoid embedding the relay secret in invites.\n"
            )
        else:
            sys.stderr.write(
                f"[quorus] WARN: /v1/join/mint returned {r.status_code} — "
                "falling back to legacy base64 envelope.\n"
            )
    except Exception as exc:
        sys.stderr.write(
            f"[quorus] WARN: /v1/join/mint failed ({exc!r}) — "
            "falling back to legacy base64 envelope.\n"
        )

    # Legacy fallback (kept ONLY for compatibility with very old relays).
    import base64 as _b64

    payload = json.dumps(
        {"relay_url": relay_url, "secret": secret, "room": room}
    )
    return "quorus_join_" + _b64.urlsafe_b64encode(payload.encode()).decode()


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
        # -1 means "no room selected" → render a clean welcome view at startup
        # instead of auto-loading whichever room happens to be index 0. Once the
        # user presses Tab/arrows or types /join, this advances to a real index.
        self.selected_room_idx: int = -1
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
        # Pending workspace switch requested via /workspace. The main loop
        # checks this on each tick and tears down cleanly when set.
        self._pending_switch: str | None = None

    def request_workspace_switch(self, slug: str) -> None:
        """Signal the main loop to tear down and switch to *slug*."""
        with self._lock:
            self._pending_switch = slug

    def pending_workspace_switch(self) -> str | None:
        with self._lock:
            return self._pending_switch

    def clear_workspace_switch(self) -> None:
        with self._lock:
            self._pending_switch = None

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
        """Refresh the room list while preserving the user's current selection.

        Why preserving by name matters: the polling thread calls this
        every few seconds. Without name-tracking, a refetch that returns
        rooms in a different order silently shifts ``selected_room_idx``
        to point at a different room.

        Why preserving when the room is MISSING from the new list also
        matters: the relay's GET /rooms returns ``list_for_member`` for
        regular auth, which can transiently omit the user's current room
        when a background agent's membership state is mid-update. We saw
        this as "TUI keeps switching me in and out of rooms randomly."
        Fix: when prev_name is missing from the new list, KEEP the old
        room dict by re-injecting it at the front of the list. The user
        stays put. Real removals (explicit /destroy or Esc) bypass this
        path because their handlers set ``selected_room_idx = -1``
        directly before calling ``set_rooms``.
        """
        with self._lock:
            prev_name = ""
            prev_room: dict | None = None
            if 0 <= self.selected_room_idx < len(self.rooms):
                prev_room = self.rooms[self.selected_room_idx]
                prev_name = prev_room.get("name") or prev_room.get("id") or ""
            if prev_name:
                for i, r in enumerate(rooms):
                    if (r.get("name") or r.get("id") or "") == prev_name:
                        self.rooms = rooms
                        self.selected_room_idx = i
                        return
                # Room is missing from the new list. If welcome is the
                # active state (no prior selection) we wouldn't be here —
                # this branch only fires when the user *had* selected a
                # room and the relay forgot it. Re-inject the stale room
                # dict so the strip still shows it and the user stays in
                # the chat. The next poll will most likely return it
                # again; if not, the user can /destroy or Esc.
                merged = [prev_room] + list(rooms) if prev_room else list(rooms)
                self.rooms = merged
                self.selected_room_idx = 0 if prev_room else -1
                return
            self.rooms = rooms

    def get_rooms(self) -> list[dict]:
        with self._lock:
            return list(self.rooms)

    def get_selected_room(self) -> Optional[dict]:
        with self._lock:
            if not self.rooms or self.selected_room_idx < 0:
                return None
            idx = max(0, min(self.selected_room_idx, len(self.rooms) - 1))
            return self.rooms[idx]

    def _selected_name_locked(self) -> str:
        """Current room's name. Must be called inside the lock."""
        if not self.rooms or self.selected_room_idx < 0:
            return ""
        idx = max(0, min(self.selected_room_idx, len(self.rooms) - 1))
        r = self.rooms[idx]
        return r.get("name") or r.get("id") or ""

    def select_next(self) -> None:
        with self._lock:
            if self.rooms:
                # Advance from welcome (-1) to first room (0), then wrap normally.
                if self.selected_room_idx < 0:
                    self.selected_room_idx = 0
                else:
                    self.selected_room_idx = (self.selected_room_idx + 1) % len(self.rooms)
                # Entering a room counts as reading it.
                self.unread.pop(self._selected_name_locked(), None)

    def select_prev(self) -> None:
        with self._lock:
            if self.rooms:
                # Going prev from welcome (-1) lands on last room.
                if self.selected_room_idx < 0:
                    self.selected_room_idx = len(self.rooms) - 1
                else:
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
        """Return the currently-selected room name, or empty string.

        Returns empty when no rooms exist OR the welcome state is active
        (selected_room_idx < 0).
        """
        with self._lock:
            if not self.rooms or self.selected_room_idx < 0:
                return ""
            idx = max(0, min(self.selected_room_idx, len(self.rooms) - 1))
            r = self.rooms[idx]
            return r.get("name") or r.get("id") or ""

    def snapshot_render_state(
        self,
    ) -> tuple[list[dict], int, Optional[dict], str]:
        """Atomically snapshot (rooms, selected_idx, selected_room, name).

        Why: the render loop used to call get_rooms(), get_selected_room(),
        and read selected_room_idx in three separate lock acquisitions. The
        polling thread's set_rooms() can re-order the room list AND update
        selected_room_idx between those acquisitions, which silently
        highlighted the wrong room — observed as "the TUI keeps moving me
        into different rooms randomly." Read everything in one lock so the
        render is internally consistent.
        """
        with self._lock:
            rooms = list(self.rooms)
            idx = self.selected_room_idx
            if not rooms or idx < 0:
                return rooms, idx, None, ""
            idx = max(0, min(idx, len(rooms) - 1))
            r = rooms[idx]
            name = r.get("name") or r.get("id") or ""
            return rooms, idx, r, name

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
        line.append(f"  [{hhmm}] ", style="ts")
    else:
        line.append("  ", style="muted")
    line.append(f"@{sender}", style=f"bold {sender_style}")
    line.append(": ", style="dim")
    # Themed 'bright' instead of literal 'white' — keeps the line
    # legible on light terminals and matches the rest of the surface.
    line.append(content, style="bright")
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
    workspace_label: str = "",
    console_width: int | None = None,
    chat_identity: str = "",
) -> Text:
    """Premium one-line header.

    Layout — left-aligned identity, right-aligned status:

      ``  @arav · workspace · 3 rooms · 12 unread        relay  ⏵ 42ms``

    The literal "quorus" word is dropped — the brand lives in the
    banner. Header now carries live signal only: identity, room count,
    unread badge, host, connection pulse, latency. We omit the
    textual status when latency is known (silence reads cleaner than
    a stale "Connecting…").

    `chat_identity` is the display-only short form of the participant
    (e.g. ``arav`` for an ``arav-codex`` profile). When set, the header
    shows ``@arav`` even though the wire still carries ``agent_name``
    (the relay enforces JWT.sub == from_name; see _send_message). Falls
    back to ``agent_name`` when not set.
    """
    from rich.text import Text as _Text

    from .render import SEP, two_col

    display_relay = (
        relay_url.replace("http://", "").replace("https://", "")
    )
    dot_style = "bold success" if connected else "bold error"

    # Left half — identity + counts. Display the short chat_identity
    # ("@arav") when set; the wire still carries agent_name ("arav-codex")
    # — the relay rejects from_name != JWT.sub, so display and transport
    # are intentionally decoupled here.
    display_name = chat_identity if chat_identity else agent_name
    left = _Text("  ")
    left.append(f"@{display_name}", style="bold agent")
    if workspace_label:
        left.append(SEP, style="dim")
        left.append(workspace_label, style="accent")
    if room_count:
        left.append(SEP, style="dim")
        left.append(
            f"{room_count} {'room' if room_count == 1 else 'rooms'}",
            style="muted",
        )
    if unread_total:
        left.append(SEP, style="dim")
        left.append(f"{unread_total} unread", style="bold room")

    # Right half — relay host + connection pulse + latency.
    right = _Text()
    if display_relay:
        right.append(display_relay, style="muted")
        right.append("  ")
    right.append("⏵", style=dot_style)
    if latency_ms is not None:
        right.append(f" {latency_ms}ms", style="muted")
    elif status and not connected:
        # Only surface a textual status when something's wrong.
        right.append(f" {status}", style="muted")

    # Right-align by padding to terminal width. Fall back to a safe
    # synthetic width when the caller didn't supply one (mostly tests).
    band = console_width or max(80, left.cell_len + right.cell_len + 4)
    return two_col(left, right, total_width=band)


def _render_room_strip(
    rooms: list[dict],
    selected_idx: int,
    *,
    unread_by_room: dict[str, int] | None = None,
) -> Text:
    """Inline room strip with active-pill + unread dots.

    ``  #dev•3   #design   #ops•1`` — the active room is bold amber,
    idle rooms fade to subtle (still readable, doesn't compete).
    Unread badge renders inline as ``•N`` so the eye finds new
    activity without a border or panel.
    """
    unread_by_room = unread_by_room or {}

    if not rooms:
        # Warm empty state — invitation, not statement of fact.
        return Text.from_markup(
            "  [muted]No rooms yet. Press[/] [kbd][n][/] "
            "[muted]or run[/] [accent]/new <name>[/][muted].[/]"
        )

    strip = Text("  ")
    for i, room in enumerate(rooms):
        name = room.get("name") or room.get("id", "?")
        is_selected = i == selected_idx
        unread = unread_by_room.get(name, 0)
        if is_selected:
            # Active room: bold amber — the canonical "you are here" signal.
            strip.append(f"#{name}", style="bold room")
        else:
            # Idle rooms fade to subtle — readable but recessed.
            strip.append(f"#{name}", style="subtle")
        if unread and not is_selected:
            strip.append(f"•{unread}", style="bold room")
        if i < len(rooms) - 1:
            # Tighter spacing than the previous "   ·   " separator —
            # the centered dot competes visually with room names.
            strip.append("   ", style="dim")
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
    line.append(f"[{hhmm}]", style="ts")
    line.append("  ")
    line.append(f"@{sender}", style=f"bold {sender_color}")
    line.append("  ")
    # 'bright' for own messages (eye-anchor), 'subtle' for others — readable
    # but recessed so the typing column stays the visual focus.
    line.append(content, style="bright" if is_me else "subtle")
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
    line.append(content, style="bright" if is_me else "subtle")
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
            glyph="◌",
            title="No room selected",
            cta="Press Tab to cycle, or run  /new <name>",
        )
    if not messages:
        return _empty_card(
            console_width,
            glyph="◌",
            title=f"#{room_name} is quiet",
            cta="Say hello to get the conversation started.",
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
    """Print the help overlay — keybinds, slash commands, room navigation."""
    console.print()
    console.print(Text.from_markup("  [bold primary]Navigation[/]"))
    nav_rows = [
        ("Tab",          "cycle to the next room"),
        ("Esc",          "back to the welcome / home view"),
        ("↑ / ↓",        "walk through your input history"),
        ("?  or  /help", "show this screen"),
        ("Ctrl+C",        "quit"),
    ]
    for kb, desc in nav_rows:
        console.print(
            Text.from_markup(f"  [bold agent]{kb:<22}[/]  [dim]{desc}[/]")
        )

    console.print()
    console.print(Text.from_markup(
        "  [bold primary]Slash commands[/]  [dim]· type / to see options[/]"
    ))
    slash_rows = [
        ("/home",            "return to the welcome view"),
        ("/new <name>",      "create a new room (alias /create)"),
        ("/join <room>",     "switch to a room by name"),
        ("/switch <room>",   "alias for /join"),
        ("/rooms",           "list all rooms with a numbered menu"),
        ("/share <room>",    "mint a portable invite token"),
        ("/delete <room>",   "destroy a room (confirmation required)"),
        ("/invite",          "show how to share the current room"),
        ("/workspace",       "list or switch workspaces"),
        ("/status",          "connection and relay info"),
        ("/clear",           "clear the chat pane"),
        ("/quit",            "close the hub"),
    ]
    for cmd, desc in slash_rows:
        console.print(
            Text.from_markup(f"  [bold primary]{cmd:<22}[/]  [dim]{desc}[/]")
        )

    console.print()
    console.print(Text.from_markup("  [bold primary]Sending messages[/]"))
    console.print(Text.from_markup(
        "  [dim]Anything that isn't a command is sent as a chat message "
        "to the active room.[/]"
    ))
    console.print(Text.from_markup(
        "  [dim]Plain-English shortcuts also work:[/] "
        "[muted]create dev  ·  join general  ·  quit[/]"
    ))
    console.print()


# ── Slash commands ────────────────────────────────────────────────────────────
# Single source of truth for the slash-command table. Each entry:
#   "/verb": (one-line description, handler)
# Handler signature:
#   handler(arg, state, relay_url, secret, agent_name, console) -> True | "__QUIT__"


def _print_room_menu(state: "HubState", console: "Console") -> None:
    """Print a numbered room list. User can type the number to switch."""
    rooms = state.get_rooms()
    if not rooms:
        console.print(Text.from_markup("  [dim]no rooms yet · /create <name>[/]"))
        return
    console.print()
    console.print(Text.from_markup("  [bold primary]Rooms[/]"))
    selected_name = state.selected_room_name()
    with state._lock:
        unread = dict(state.unread)
    for i, room in enumerate(rooms, 1):
        name = room.get("name") or room.get("id", "?")
        is_selected = name == selected_name
        badge = unread.get(name, 0)
        line = Text(f"  {i}  ")
        if is_selected:
            line.append(f"#{name}", style="bold room")
            line.append("  active", style="dim success")
        else:
            line.append(f"#{name}", style="dim")
        if badge and not is_selected:
            line.append(f"  •{badge} unread", style="bold room")
        console.print(line)
    console.print()


def _print_slash_hint(console: "Console") -> None:
    """Autocomplete hint shown when user submits a bare '/'.

    Lists each slash command with its one-line description so it's
    actually discoverable (instead of just a jumble of verbs).
    """
    console.print()
    console.print(Text.from_markup(
        "  [bold primary]Slash commands[/]  [dim]· type the verb, then Enter[/]"
    ))
    for verb, (desc, _handler) in SLASH_COMMANDS.items():
        console.print(
            Text.from_markup(f"  [bold primary]{verb:<12}[/]  [dim]{desc}[/]")
        )
    console.print()
    console.print(Text.from_markup(
        "  [dim]Press Enter on an empty prompt to return to chat.[/]"
    ))
    console.print()


def _slash_help(arg, state, relay_url, secret, agent_name, console):
    del arg, relay_url, secret, agent_name
    _print_help(console)
    _print_room_menu(state, console)
    return True


def _slash_rooms(arg, state, relay_url, secret, agent_name, console):
    del arg, agent_name
    rooms = _fetch_rooms(relay_url, secret)
    if rooms:
        state.set_rooms(rooms)
        state.set_status_bar(f"{len(rooms)} room(s) loaded")
    else:
        state.set_status_bar("no rooms (or relay unreachable)")
    _print_room_menu(state, console)
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


def _slash_share(arg, state, relay_url, secret, agent_name, console):
    """`/share <room>` — mint a portable invite token and show the share card.

    Mirrors the [s] in-room shortcut. Defaults to the currently-selected
    room. Renders the polished share card from chat.render_share_card and
    blocks until the user presses ``c`` (copy) or any key (dismiss).
    """
    del agent_name
    from . import slash as _slash

    target = (arg.strip() or state.selected_room_name()).lstrip("#")
    if not target:
        state.set_status_bar("usage: /share <room>")
        return True
    rooms_snap = state.get_rooms()
    if not any((r.get("name") or "") == target for r in rooms_snap):
        state.set_status_bar(f"no room '{target}' — try /rooms")
        return True
    code = _mint_join_token(relay_url, secret, target)
    install_url = _share_install_url(code)
    status = _slash.run_share_flow(
        console=console,
        room_name=target,
        code=code,
        install_url=install_url,
    )
    state.set_status_bar(status)
    return True


def _share_install_url(code: str) -> str | None:
    """One-line install URL for the share card.

    Server-issued short codes (no underscores, no `quorus_join_` prefix)
    fit nicely under the q.dev/r/<code>.sh installer; the legacy base64
    envelope is too long for a curl URL so we omit it.
    """
    if not code or code.startswith("quorus_join_"):
        return None
    return f"curl -sSL https://q.dev/r/{code}.sh | sh"


def _slash_delete(arg, state, relay_url, secret, agent_name, console):
    """`/delete <room>` — destroy a room. Requires inline confirmation."""
    target = (arg.strip() or state.selected_room_name()).lstrip("#")
    if not target:
        state.set_status_bar("usage: /delete <room>")
        return True
    rooms_snap = state.get_rooms()
    if not any((r.get("name") or "") == target for r in rooms_snap):
        state.set_status_bar(f"no room '{target}'")
        return True
    console.print()
    confirm = Prompt.ask(f"  Type '{target}' to confirm delete", default="").strip()
    if confirm != target:
        state.set_status_bar("Cancelled — confirmation mismatch.")
        return True
    status, detail = _destroy_room(relay_url, secret, target, agent_name)
    if status == "ok":
        state.set_status_bar(f"Deleted #{target}")
        with state._lock:
            state.selected_room_idx = -1
        state.set_messages([])
        state.set_rooms(_fetch_rooms(relay_url, secret))
    elif status == "auth":
        state.set_status_bar(
            f"Not authorized to delete #{target}"
            + (f" — {detail}" if detail else "")
        )
    elif status == "missing":
        state.set_status_bar(f"#{target} no longer exists.")
    elif status == "unreachable":
        state.set_status_bar(f"Can't reach relay at {relay_url}.")
    else:
        state.set_status_bar(
            f"Couldn't delete #{target} — {status}"
            + (f": {detail}" if detail else "")
        )
    return True


def _slash_new(arg, state, relay_url, secret, agent_name, console):
    """`/new <name>` — alias for /create, matches the [n] welcome shortcut."""
    return _slash_create(arg, state, relay_url, secret, agent_name, console)


def _slash_home(arg, state, relay_url, secret, agent_name, console):
    """`/home` — return to the welcome view (same as Esc)."""
    del arg, relay_url, secret, agent_name, console
    with state._lock:
        state.selected_room_idx = -1
    state.set_messages([])
    state.set_status_bar("")
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


def _slash_switch(arg, state, relay_url, secret, agent_name, console):
    return _slash_join(arg, state, relay_url, secret, agent_name, console)


def _print_workspace_menu(console) -> None:
    from quorus.profiles import ProfileManager
    pm = ProfileManager()
    slugs = pm.list()
    current = pm.current()
    console.print()
    console.print(Text.from_markup("  [bold primary]Workspaces[/]"))
    if not slugs:
        console.print(Text.from_markup(
            "  [dim]no workspaces — run [bold]quorus login[/] to add one[/]"
        ))
        console.print()
        return
    for slug in slugs:
        data = pm.get(slug) or {}
        label = (
            data.get("workspace_label")
            or data.get("instance_name")
            or slug
        )
        marker = "●" if slug == current else " "
        line = Text(f"  {marker}  ")
        if slug == current:
            line.append(slug, style="bold room")
            line.append("  active", style="dim success")
        else:
            line.append(slug, style="dim")
        line.append(f"  {label}", style="dim")
        console.print(line)
    console.print()
    console.print(Text.from_markup(
        "  [dim]switch: /workspace <slug>  ·  add: /workspace add[/]"
    ))
    console.print()


def _slash_workspace(arg, state, relay_url, secret, agent_name, console):
    del relay_url, secret, agent_name
    from quorus.profiles import ProfileManager
    target = arg.strip()
    pm = ProfileManager()

    if not target:
        _print_workspace_menu(console)
        return True

    if target == "add":
        state.set_status_bar(
            "run `quorus login` in a second terminal to add a workspace"
        )
        return True

    if pm.get(target) is None:
        state.set_status_bar(f"no such workspace: {target} — /workspace to list")
        return True

    state.request_workspace_switch(target)
    state.set_status_bar(f"switching to {target}...")
    return True


def _slash_leave(arg, state, relay_url, secret, agent_name, console):
    from . import slash as _slash
    return _slash.slash_leave(arg, state, relay_url, secret, agent_name, console)


def _slash_info(arg, state, relay_url, secret, agent_name, console):
    from . import slash as _slash
    return _slash.slash_info(arg, state, relay_url, secret, agent_name, console)


def _slash_me(arg, state, relay_url, secret, agent_name, console):
    from . import slash as _slash
    return _slash.slash_me(arg, state, relay_url, secret, agent_name, console)


def _slash_mute(arg, state, relay_url, secret, agent_name, console):
    from . import slash as _slash
    return _slash.slash_mute(arg, state, relay_url, secret, agent_name, console)


def _slash_identity(arg, state, relay_url, secret, agent_name, console):
    """`/identity human|agent|<name>` — change the from_name for outbound
    messages. Persists to the active profile so the choice survives
    restarts. Without args, shows the current identity.

    Useful when a profile is for an agent (instance_name=arav-codex)
    but the human owner is typing — they don't want to be tagged as
    their own agent. Auto-default already strips agent suffixes; this
    is the manual override.
    """
    del relay_url, secret  # unused — pure local state
    target = arg.strip().lower()
    profile = ConfigManager().load() or {}
    current_identity = profile.get("chat_identity") or agent_name

    if not target:
        state.set_status_bar(
            f"identity: @{current_identity} (profile @{agent_name}). "
            "Use: /identity human | /identity agent | /identity <name>"
        )
        return True

    if target == "human":
        new_identity = _strip_agent_suffix(agent_name)
    elif target == "agent":
        new_identity = agent_name
    else:
        # Treat as a literal name. Allow only [A-Za-z0-9_-], non-empty,
        # ≤64 chars — same shape as agent registration.
        import re as _re
        if not _re.match(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,63}$", target):
            state.set_status_bar(
                f"invalid identity '{target}' — letters/numbers/-_ only, "
                "must start with a letter/number/_"
            )
            return True
        new_identity = target

    profile["chat_identity"] = new_identity
    try:
        ConfigManager().save(profile)
        state.set_status_bar(
            f"identity → @{new_identity} (was @{current_identity})"
        )
        # NOTE: doesn't update the live agent_name in the running loop —
        # restart for the change to take effect on outbound messages.
        console.print(
            f"  [dim]Restart the TUI to use @{new_identity} for sends[/]"
        )
    except Exception as e:  # noqa: BLE001
        state.set_status_bar(f"could not persist: {e}")
    return True


SLASH_COMMANDS: dict[str, tuple[str, callable]] = {
    "/help":       ("show keybinds + all commands",        _slash_help),
    "/home":       ("return to the welcome view",           _slash_home),
    "/rooms":      ("list rooms with numbered menu",        _slash_rooms),
    "/join":       ("/join <room> — switch to a room",      _slash_join),
    "/switch":     ("/switch <room> — alias for /join",     _slash_switch),
    "/new":        ("/new <name> — alias for /create",      _slash_new),
    "/create":     ("/create <name> — make a new room",     _slash_create),
    "/share":      ("/share <room> — mint a join token",    _slash_share),
    "/leave":      ("/leave — exit current room",           _slash_leave),
    "/delete":     ("/delete <room> — destroy a room",      _slash_delete),
    "/invite":     ("show how to share the current room",   _slash_invite),
    "/info":       ("members + last-active for this room",  _slash_info),
    "/me":         ("/me <action> — IRC-style action line", _slash_me),
    "/mute":       ("/mute <duration> — silence (preview)", _slash_mute),
    "/identity":   ("/identity human|agent|<name> — set from_name", _slash_identity),
    "/rename":     ("/rename <name> — alias for /identity <name>", _slash_identity),
    "/name":       ("/name <name> — alias for /identity <name>",   _slash_identity),
    "/workspace":  ("list / switch workspaces",             _slash_workspace),
    "/status":     ("connection + relay info",              _slash_status),
    "/clear":      ("clear the chat pane",                  _slash_clear),
    "/quit":       ("close the hub",                        _slash_quit),
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


# ── Input reader ──────────────────────────────────────────────────────────────

# In-progress input that survives a render-tick timeout. Without this, every
# 2s redraw wiped the user's half-typed message — the source of "the text
# keeps getting erased" complaints. _read_input pulls from + writes back to
# this buffer on every call.
_PENDING_INPUT_BUF: list[str] = []

# Last-keystroke wall-clock used as a "is the human at the keyboard?" proxy
# for OS-level notification gating. Updated in _read_input on every real
# keystroke; consulted in _sse_loop before deciding to fire a banner.
# 30s of keystroke silence ⇒ TUI is effectively unfocused and we should
# surface inbound @-mentions / DMs through the OS notification center.
_LAST_KEYSTROKE_AT: float = 0.0
_FOCUS_IDLE_THRESHOLD_S = 30.0


def _stamp_focus_now() -> None:
    """Mark the TUI as foregrounded right now (called from _read_input)."""
    global _LAST_KEYSTROKE_AT
    _LAST_KEYSTROKE_AT = time.monotonic()


def _tui_is_unfocused(now: float | None = None) -> bool:
    """Return True iff no keystroke has hit _read_input in >30s.

    Falls back to "unfocused" before the first keystroke ever lands so
    notifications fire on the very first message of a session — better
    UX than silence until the user types.
    """
    clock = now if now is not None else time.monotonic()
    if _LAST_KEYSTROKE_AT == 0.0:
        return True
    return (clock - _LAST_KEYSTROKE_AT) >= _FOCUS_IDLE_THRESHOLD_S


# Special sentinel strings returned by _read_input() for non-text keys.
_KEY_UP      = "__UP__"
_KEY_DOWN    = "__DOWN__"
_KEY_TAB     = "__TAB__"
_KEY_ESC     = "__ESC__"       # Esc — return to welcome / home view
_KEY_QUIT    = "__QUIT_KEY__"
_KEY_TIMEOUT = "__TIMEOUT__"   # no keypress within render tick — re-render


def _read_input(
    prompt: str = "❯ ",
    history: list[str] | None = None,
) -> str:
    """Read one line of input, handling special keys properly.

    With readchar: intercepts arrow keys and Tab. Up/Down walk the
    submitted-input history in-place (readline-style) — the current line
    is replaced with a previous entry, and the user can edit or submit.
    Tab is returned as a sentinel so the caller can cycle rooms.

    Without readchar (fallback): behaves like plain input(). Arrow keys
    will still show ^[[A in this path.
    """
    if not _READCHAR_AVAILABLE:
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return _KEY_QUIT

    hist = history or []
    # hist_idx == len(hist) means "not browsing history — editing a
    # fresh line". Walking UP decrements; DOWN increments back to the end.
    hist_idx = len(hist)
    draft = ""  # the in-progress line the user was typing before UP

    def _redraw_line(buf_list: list[str]) -> None:
        # Clear the current input line and redraw prompt + buffer.
        # \r returns to column 0, \x1b[K erases to end of line.
        sys.stdout.write("\r\x1b[K")
        sys.stdout.write(prompt)
        sys.stdout.write("".join(buf_list))
        sys.stdout.flush()

    # Optional diagnostics — set QUORUS_DEBUG=1 in the env to log every
    # keystroke + redraw + timeout to /tmp/quorus-input.log so we can
    # diagnose "text disappears as I type" reports without guessing.
    _debug = bool(os.environ.get("QUORUS_DEBUG"))

    def _dlog(msg: str) -> None:
        if not _debug:
            return
        try:
            with open("/tmp/quorus-input.log", "a") as fp:
                import time as _t
                fp.write(f"[{_t.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    # CRITICAL: put stdin in non-canonical, no-echo mode for the duration
    # of this call. Two separate problems combine if we don't:
    #   1. Default cooked mode buffers chars until Enter — `select()`
    #      never sees them, the 2s tick fires, the screen wipes, and the
    #      user sees "type → appears (terminal echo) → vanishes (our wipe)"
    #   2. cbreak mode (the obvious fix) leaves ECHO on, so the terminal
    #      echoes each char AND our `sys.stdout.write(key)` writes it
    #      again — producing doubled characters on every keystroke.
    # The fix is the same dance readchar does internally: clear ICANON
    # (line buffering) AND ECHO (terminal local echo) but KEEP ISIG so
    # Ctrl+C still raises KeyboardInterrupt.
    import termios
    _stdin_fd = sys.stdin.fileno() if sys.stdin.isatty() else -1
    _saved_termios = None
    if _stdin_fd >= 0:
        try:
            _saved_termios = termios.tcgetattr(_stdin_fd)
            new_attrs = list(_saved_termios)
            # lflag is index 3: clear ICANON + ECHO, keep ISIG.
            new_attrs[3] = new_attrs[3] & ~(termios.ICANON | termios.ECHO)
            # cc array (index 6): VMIN=1 (block until 1 char), VTIME=0
            # so reads block correctly even with select() in front.
            new_attrs[6] = list(new_attrs[6])
            new_attrs[6][termios.VMIN] = 1
            new_attrs[6][termios.VTIME] = 0
            termios.tcsetattr(_stdin_fd, termios.TCSADRAIN, new_attrs)
        except (termios.error, OSError, AttributeError):
            _saved_termios = None  # not a real tty (CI / pipe) — fall through

    # Restore in-progress buffer from a prior render-tick timeout so the user
    # never loses what they were typing when the 2s redraw fires.
    buf: list[str] = list(_PENDING_INPUT_BUF)
    _PENDING_INPUT_BUF.clear()
    _redraw_line(buf)
    _dlog(f"_read_input start; restored buf={buf!r}; tty_raw={_saved_termios is not None}")
    try:
        while True:
            import select as _sel
            ready, _, _ = _sel.select([sys.stdin], [], [], RENDER_TICK_S)
            if not ready:
                _dlog(f"select timeout; buf={buf!r}")
                if buf:
                    # User is composing — never trigger a redraw mid-typing.
                    continue
                _PENDING_INPUT_BUF.clear()
                sys.stdout.write("\r\x1b[K")
                sys.stdout.flush()
                _dlog("returning _KEY_TIMEOUT")
                return _KEY_TIMEOUT
            # Read directly from stdin instead of readchar.readkey() — readchar
            # saves+restores termios on EVERY call, which fights with the
            # outer raw-mode setup we did once and produces "key lag" where
            # nothing appears for ~50ms per char. Since stdin is already in
            # ICANON-off + ECHO-off mode, a single read(1) is correct and
            # fast. Multi-byte escape sequences (arrows, F-keys) start with
            # ESC (0x1b); we peek with select(timeout=0.05) to gather the
            # rest of the sequence without blocking on a lone ESC press.
            try:
                _b = sys.stdin.read(1)
            except (OSError, ValueError):
                _b = ""
            if not _b:
                continue
            # Real keystroke — used by _sse_loop to decide whether the user
            # is "present" or whether to surface an OS notification.
            _stamp_focus_now()
            if _b == "\x1b":
                # Possible escape sequence — peek for trailing bytes.
                _follow = ""
                _sel_more, _, _ = _sel.select([sys.stdin], [], [], 0.05)
                while _sel_more:
                    _follow += sys.stdin.read(1)
                    _sel_more, _, _ = _sel.select([sys.stdin], [], [], 0.005)
                key = "\x1b" + _follow
            elif _b in ("\x7f", "\x08"):
                key = readchar.key.BACKSPACE
            elif _b in ("\r", "\n"):
                key = readchar.key.ENTER
            elif _b == "\t":
                key = readchar.key.TAB
            elif _b == "\x03":
                key = readchar.key.CTRL_C
            elif _b == "\x04":
                key = readchar.key.CTRL_D
            else:
                key = _b
            if key in (readchar.key.ENTER, "\r", "\n"):
                # Multi-line paste detection: if more bytes are pending in
                # stdin within 5ms, this \n is part of a paste — append it
                # to the buffer and keep reading instead of treating it as
                # a submit. Without this, a paste of N lines becomes N
                # separate messages (real bug observed: pasting 19-line
                # text → 19 separate room messages).
                #
                # 5ms threshold: human keystrokes are >100ms apart, paste
                # bytes arrive in microseconds. Safe for both real typists
                # and bracketed-paste-unaware terminals (iTerm2, VS Code,
                # macOS Terminal).
                _ready_more, _, _ = _sel.select([sys.stdin], [], [], 0.005)
                if _ready_more:
                    buf.append("\n")
                    # Don't redraw the single-line composer for multi-line
                    # input — collect silently. The user will see their
                    # full message render in the chat after submit.
                    continue
                _PENDING_INPUT_BUF.clear()  # commit — the line is submitted
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(buf).strip()
            if key == readchar.key.UP:
                if not hist:
                    continue
                if hist_idx == len(hist):
                    # Stash the in-progress draft so DOWN can restore it.
                    draft = "".join(buf)
                if hist_idx > 0:
                    hist_idx -= 1
                    buf = list(hist[hist_idx])
                    _redraw_line(buf)
                continue
            if key == readchar.key.DOWN:
                if not hist:
                    continue
                if hist_idx < len(hist) - 1:
                    hist_idx += 1
                    buf = list(hist[hist_idx])
                    _redraw_line(buf)
                elif hist_idx == len(hist) - 1:
                    hist_idx = len(hist)
                    buf = list(draft)
                    _redraw_line(buf)
                continue
            if key == readchar.key.TAB:
                sys.stdout.write("\n")
                sys.stdout.flush()
                return _KEY_TAB
            # Esc on an empty input → "back to welcome / home". If the user
            # has already typed something we ignore Esc so it doesn't nuke
            # an in-progress message accidentally.
            if key == readchar.key.ESC and not buf:
                sys.stdout.write("\n")
                sys.stdout.flush()
                return _KEY_ESC
            if key in (readchar.key.CTRL_C, readchar.key.CTRL_D):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return _KEY_QUIT
            if key in (readchar.key.BACKSPACE, "\x7f"):
                if buf:
                    buf.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            # Ignore other control/escape sequences (F-keys, Home, End…).
            if key.startswith("\x1b") or ord(key[0]) < 32:
                continue
            buf.append(key)
            sys.stdout.write(key)
            sys.stdout.flush()
            _dlog(f"keystroke {key!r}; buf now {buf!r}")
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n")
        sys.stdout.flush()
        return _KEY_QUIT
    finally:
        # Restore the terminal to its prior mode so the parent shell
        # behaves normally after we exit.
        if _saved_termios is not None and _stdin_fd >= 0:
            try:
                termios.tcsetattr(_stdin_fd, termios.TCSADRAIN, _saved_termios)
            except (termios.error, OSError):
                pass


# ── Main hub loop ─────────────────────────────────────────────────────────────

def _main_input_loop(
    *,
    console,
    state: "HubState",
    relay_url: str,
    secret: str,
    agent_name: str,
    workspace_label: str = "",
    # DISPLAY-ONLY identity. `chat_identity` flows into the header
    # ("@arav" instead of "@arav-codex") and the /identity slash command,
    # but NEVER reaches the wire — the relay enforces
    # JWT.sub == from_name and rejects mismatches with 403 "Cannot send
    # as another user". Outbound messages and the optimistic echo both
    # use `agent_name`. Tenant-aliasing on the relay (codex's lane)
    # would let us collapse this; until then, display and transport are
    # intentionally decoupled here.
    chat_identity: str = "",
) -> "str | tuple[str, str]":
    """The render+input loop. Returns 'quit' or ('switch', slug).

    Body of the old run_hub main loop, plus a per-tick check for
    pending workspace-switch requests set by /workspace.

    `chat_identity` is the display-only short form (e.g., @arav for an
    @arav-codex profile). It's used for the header and the /identity
    slash command but the wire (and optimistic echo) stays on
    `agent_name` because the relay rejects from_name != JWT.sub.
    """
    if not chat_identity:
        chat_identity = agent_name
    _full_clear(console)
    last_render = 0.0
    # Hold auto-redraw until the user submits next input. Used after we
    # print transient output (help, slash hint, room menu) so the redraw
    # tick doesn't clear it out from under them.
    hold_render = False
    # Sticky input-quiet hold: every key press bumps this timestamp
    # forward by INPUT_QUIET_HOLD_S. Idle redraws skip while now < this.
    # Protects long-form typing AND copy/paste from being wiped.
    input_quiet_until: float = 0.0
    # Readline-style input history: last N lines the user submitted.
    # Arrow Up/Down walk this in _read_input().
    input_history: list[str] = []
    HISTORY_MAX = 100

    try:
        while True:
            # Pending workspace switch? Exit the loop so _run_session
            # can tear down threads and return ("switch", slug).
            pending = state.pending_workspace_switch()
            if pending is not None:
                state.clear_workspace_switch()
                return ("switch", pending)

            now = time.monotonic()

            # Redraw if poll interval passed or first render — UNLESS the
            # user is still mid-typing (input_quiet_until is in the future).
            # SSE arrival forces last_render=0 elsewhere, so real chat events
            # still appear immediately; this only blocks the cosmetic tick.
            if (
                not hold_render
                and now >= input_quiet_until
                and now - last_render >= POLL_S
            ):
                connected, conn_status = state.get_connection()
                # Atomic snapshot: rooms + selected_idx must come from the
                # same lock acquisition. Otherwise the polling thread can
                # re-order rooms between get_rooms() and reading the index,
                # and the render highlights the wrong room.
                rooms_snap, selected_idx, selected, room_name = (
                    state.snapshot_render_state()
                )
                msgs_snap = state.get_messages()
                status_bar = state.get_status_bar()

                from rich.rule import Rule as _Rule

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

                _full_clear(console)
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
                        workspace_label=workspace_label,
                        console_width=console_width,
                        chat_identity=chat_identity,
                    )
                )
                console.print(_Rule(style="dim"))

                if selected is None:
                    # Welcome / Home view — banner, action menu, chats panel.
                    # Skips the room strip + nav hint + chat feed entirely so
                    # the welcome surface owns the screen between header and
                    # prompt. The instant the user picks a room (Tab/Enter/
                    # /join), the else-branch below takes over.
                    _welcome.render_welcome(
                        console,
                        rooms=rooms_snap,
                        unread_by_room=unread_by_room,
                        selected_room_name=room_name,
                        messages=msgs_snap,
                        agent_name=agent_name,
                        workspace_label=workspace_label,
                    )
                else:
                    # iMessage-style in-room view: pinned app-bar on top,
                    # bubble feed in the middle, slim composer hint above
                    # the prompt. The room strip is folded into the app-bar
                    # actions so the screen real estate stays focused on
                    # the conversation, not the navigation chrome.
                    from . import chat as _chat

                    members = (selected.get("members") or []) if selected else []
                    last_active = _chat.last_active_label(msgs_snap)
                    console.print(
                        _chat.render_app_bar(
                            room_name=room_name,
                            member_count=len(members),
                            last_active=last_active,
                            console_width=console_width,
                            messages=msgs_snap,
                        )
                    )
                    # Inline room strip stays as a one-liner so Tab still
                    # has a visual reference. Dim & compact.
                    console.print(
                        _render_room_strip(
                            rooms_snap, selected_idx,
                            unread_by_room=unread_by_room,
                        )
                    )
                    console.print()
                    # Bubble feed — grouped, with optional unread divider.
                    first_unread = state.get_unread(room_name)
                    feed_unread_idx = (
                        max(0, len(msgs_snap) - first_unread)
                        if first_unread
                        else None
                    )
                    for feed_line in _chat.render_bubble_feed(
                        msgs_snap, room_name, agent_name,
                        console_width=console_width,
                        first_unread_index=feed_unread_idx,
                    ):
                        console.print(feed_line)
                    console.print()
                    console.print(_chat.render_composer_hint())
                # Transient status line (if any) above the bare prompt.
                status_line = _render_status_line(status_bar)
                if status_line is not None:
                    console.print(status_line)
                last_render = now

            # Bare prompt — no panel, no border.
            line = _read_input("❯ ", history=input_history)

            # Once the user submits anything, clear the render hold so the
            # UI catches up on the next tick.
            hold_render = False

            # User hit Enter — close out the current typing session so the
            # next incoming SSE message draws a fresh separator rule above
            # it (instead of silently stacking).
            state.reset_inline_rule()

            if line == _KEY_TIMEOUT:
                # Render tick fired. If the user is mid-typing (we have a
                # pending input buffer), HOLD the redraw — wiping the screen
                # erases what they're typing, even though the buffer survives
                # the round-trip. Also bump input_quiet_until so the next
                # tick stays held until INPUT_QUIET_HOLD_S of keystroke-free
                # idle has passed. Protects copy/paste sessions where the
                # user is reading and might trigger select+copy mid-tick.
                if _PENDING_INPUT_BUF:
                    hold_render = True
                    input_quiet_until = now + INPUT_QUIET_HOLD_S
                else:
                    last_render = 0
                continue

            if line == _KEY_QUIT:
                return "quit"

            # Esc → drop the user back to the welcome / home view. Pure
            # state mutation — no relay calls, no destructive side-effects.
            if line == _KEY_ESC:
                with state._lock:
                    state.selected_room_idx = -1
                state.set_messages([])
                state.set_status_bar("")
                last_render = 0
                continue

            # Tab → cycle to next room. (Arrow Up/Down now walk input
            # history inside _read_input — they never reach here.)
            if line == _KEY_TAB:
                state.select_next()
                selected = state.get_selected_room()
                if selected:
                    rname = selected.get("name") or selected.get("id", "")
                    if rname:
                        _join_room(relay_url, secret, rname, agent_name)
                        _load_history_into(state, relay_url, secret, rname)
                last_render = 0
                continue

            if not line:
                continue

            # Record in history (dedup consecutive, cap length).
            if not input_history or input_history[-1] != line:
                input_history.append(line)
                if len(input_history) > HISTORY_MAX:
                    del input_history[: len(input_history) - HISTORY_MAX]

            # ── Plain-English command parsing ──────────────────────────────────
            cmd = line.strip()
            cmd_lower = cmd.lower()

            # quit / exit
            if cmd_lower in ("quit", "exit", "q"):
                return "quit"

            # help — plain word or "?" shortcut
            if cmd_lower in ("help", "?"):
                _print_help(console)
                _print_room_menu(state, console)
                hold_render = True
                continue

            # ── Welcome-view single-key shortcuts ──────────────────────────
            # These mirror the [j] [r] [s] [d] entries in the home action
            # menu. They prompt inline for any required argument and call
            # the EXISTING relay endpoints — no new endpoints are created.
            if cmd_lower == "r":
                # /rooms — print the numbered room menu inline.
                _print_room_menu(state, console)
                hold_render = True
                continue

            if cmd_lower == "j":
                # Join by invite — paste a quorus_join_ token.
                console.print()
                token = Prompt.ask("  Paste invite token").strip()
                if not token:
                    state.set_status_bar("Cancelled — no token entered.")
                elif not token.startswith("quorus_join_"):
                    state.set_status_bar(
                        "Not a join token — expected 'quorus_join_…' prefix."
                    )
                else:
                    state.set_status_bar(
                        "Token noted — run: quorus join "
                        + token[:24] + "…  to switch workspaces."
                    )
                last_render = 0
                continue

            if cmd_lower == "s":
                # Share — the easier path the user asked for. Behavior:
                #   * In a room → mint + show card immediately, no prompt.
                #   * Welcome view, exactly one room → share that one.
                #   * Welcome view, multiple rooms → prompt for which.
                from . import slash as _slash_mod

                rooms_snap = state.get_rooms()
                target = state.selected_room_name()
                if not target and len(rooms_snap) == 1:
                    target = rooms_snap[0].get("name") or ""
                if not target:
                    console.print()
                    target = Prompt.ask(
                        "  Share which room",
                        default=(rooms_snap[0].get("name") if rooms_snap else ""),
                    ).strip().lstrip("#")
                if not target:
                    state.set_status_bar("Cancelled — no room name.")
                elif not any(
                    (r.get("name") or "") == target for r in rooms_snap
                ):
                    state.set_status_bar(
                        f"No room '{target}' — press r for the list."
                    )
                else:
                    code = _mint_join_token(relay_url, secret, target)
                    install_url = _share_install_url(code)
                    status = _slash_mod.run_share_flow(
                        console=console,
                        room_name=target,
                        code=code,
                        install_url=install_url,
                    )
                    state.set_status_bar(status)
                last_render = 0
                continue

            # In-room single-key shortcuts: i (info), m (members), l (leave).
            # Only act when the user is *inside* a room — the welcome view
            # owns its own letters (n/j/r/s/d). Skip when an in-progress
            # message would be ambiguous (we already passed the "submit"
            # gate, so single-letter input here is intentional).
            if cmd_lower in {"i", "m", "l"} and state.get_selected_room():
                from . import slash as _slash_mod

                if cmd_lower == "l":
                    _slash_mod.slash_leave(
                        "", state, relay_url, secret, agent_name, console,
                    )
                else:
                    # 'i' and 'm' both surface the info pane — members are
                    # the dominant fact in it, and there's no separate
                    # "members" endpoint to query.
                    _slash_mod.slash_info(
                        "", state, relay_url, secret, agent_name, console,
                    )
                    hold_render = True
                last_render = 0
                continue

            if cmd_lower == "d":
                # Delete <room> — destructive, gated by an explicit
                # confirmation prompt. Uses DELETE /rooms/{id}.
                console.print()
                rooms_snap = state.get_rooms()
                if not rooms_snap:
                    state.set_status_bar("No rooms to delete.")
                    last_render = 0
                    continue
                target = Prompt.ask(
                    "  Delete which room",
                    default=state.selected_room_name() or "",
                ).strip().lstrip("#")
                if not target:
                    state.set_status_bar("Cancelled — no room name.")
                elif not any(
                    (r.get("name") or "") == target for r in rooms_snap
                ):
                    state.set_status_bar(f"No room '{target}'.")
                else:
                    confirm = Prompt.ask(
                        f"  Type '{target}' to confirm",
                        default="",
                    ).strip()
                    if confirm != target:
                        state.set_status_bar("Cancelled — confirmation mismatch.")
                    else:
                        status, detail = _destroy_room(
                            relay_url, secret, target, agent_name,
                        )
                        if status == "ok":
                            state.set_status_bar(f"Deleted #{target}")
                            with state._lock:
                                state.selected_room_idx = -1
                            state.set_messages([])
                            new_rooms = _fetch_rooms(relay_url, secret)
                            state.set_rooms(new_rooms)
                        elif status == "auth":
                            state.set_status_bar(
                                f"Not authorized to delete #{target}"
                                + (f" — {detail}" if detail else "")
                            )
                        elif status == "missing":
                            state.set_status_bar(
                                f"#{target} no longer exists."
                            )
                        elif status == "unreachable":
                            state.set_status_bar(
                                f"Can't reach relay at {relay_url}."
                            )
                        else:
                            state.set_status_bar(
                                f"Couldn't delete #{target} — {status}"
                                + (f": {detail}" if detail else "")
                            )
                last_render = 0
                continue

            # Number → jump to room by index from the /rooms menu.
            if cmd.isdigit():
                idx = int(cmd) - 1
                rooms_snap = state.get_rooms()
                if 0 <= idx < len(rooms_snap):
                    target = rooms_snap[idx]
                    rname = target.get("name") or target.get("id", "")
                    state.select_by_name(rname)
                    _join_room(relay_url, secret, rname, agent_name)
                    _load_history_into(state, relay_url, secret, rname)
                    state.set_status_bar(f"switched to #{rname}")
                else:
                    state.set_status_bar(
                        f"no room #{cmd} — type /rooms to see the list"
                    )
                last_render = 0
                continue

            # Bare "/" → show slash-command hint inline, don't send as message.
            if cmd == "/":
                _print_slash_hint(console)
                hold_render = True
                continue

            # Slash-command dispatcher (Discord / Slack style). Runs BEFORE the
            # plain-English regex block so the two don't fight over short tokens.
            if cmd.startswith("/"):
                parts = cmd.split(None, 1)
                verb = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""
                # Commands that print transient output should hold the
                # redraw so the output isn't wiped on the next tick.
                # /status and /invite only set the status bar — they
                # NEED a redraw for the bar to appear.
                printing_verbs = {
                    "/help", "/rooms", "/workspace", "/share", "/delete",
                    "/info",
                }
                handled = _dispatch_slash(
                    verb, arg, state, relay_url, secret, agent_name, console,
                )
                if handled == "__QUIT__":
                    return "quit"
                if handled:
                    if verb in printing_verbs:
                        hold_render = True
                    else:
                        last_render = 0
                    continue
                state.set_status_bar(
                    f"unknown command: {verb} — try /help"
                )
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

            # IMPORTANT: post as agent_name, not chat_identity.
            # The relay enforces JWT.sub == from_name (anti-impersonation:
            # 403 "Cannot send as another user"). chat_identity is a
            # display-only suffix-strip; until the relay supports tenant
            # aliases (codex's lane), every outbound message must carry
            # the JWT subject's actual participant name.
            sent_id = _send_message(relay_url, secret, room_name, agent_name, cmd)
            if sent_id is not None:
                state.set_status_bar("")
                # Optimistic local echo — render immediately, don't wait for SSE
                # round-trip. The server-assigned ID pre-arms the dedup set so
                # when SSE (or the reconciliation poll) redelivers the same
                # message, it won't render twice.
                #
                # Echo from_name uses agent_name to MATCH the wire — the SSE
                # round-trip will return the same value and the dedup set
                # keys on (id, message_id) so the visual stays consistent
                # with what other clients see. (Showing chat_identity here
                # while the wire carries agent_name causes a flicker when
                # SSE replaces the optimistic echo with the real message.)
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
                # Distinguish "relay rejected the send" (4xx with detail —
                # e.g. 403 "Cannot send as another user") from "couldn't
                # reach the relay" (5xx/connection error). The old code
                # collapsed both into the generic hint, which lied to
                # the user when the relay was actually up and just
                # refusing the message.
                if _LAST_SEND_ERROR:
                    state.set_status_bar(_LAST_SEND_ERROR)
                else:
                    state.set_status_bar(
                        "Couldn't reach the relay. Is it running? "
                        "Try: RELAY_SECRET=x python -m quorus.relay"
                    )

            last_render = 0  # force immediate redraw after send

    except (EOFError, KeyboardInterrupt):
        return "quit"


def _run_session(
    profile: dict,
    *,
    state: "HubState | None" = None,
) -> "str | tuple[str, str]":
    """Run one workspace session. Returns 'quit' or ('switch', slug).

    The *state* parameter exists so tests can pre-seed a HubState with
    a pending switch and observe the early-exit path without running
    the full interactive loop.
    """
    console = _ui.console

    relay_url: str = profile.get("relay_url", DEFAULT_RELAY).rstrip("/")
    agent_name: str = profile.get("instance_name", "agent")
    # Auth precedence: prefer api_key over legacy relay_secret. The
    # opposite order silently breaks the TUI when a profile carries
    # BOTH (common when a fresh `quorus init --api-key` runs against a
    # profile that still has an old dev `relay_secret: "quorus-dev"`).
    # Production relay rejects the legacy secret and the user sees
    # "No rooms yet" with no error. Production api_keys are prefixed
    # `mct_` and `_get_auth_token` exchanges them for a JWT.
    api_key_val = (profile.get("api_key") or "").strip()
    legacy_secret = (profile.get("relay_secret") or "").strip()
    if api_key_val.startswith("mct_"):
        raw_secret: str = api_key_val
    else:
        raw_secret = legacy_secret or api_key_val
    workspace_label: str = profile.get("workspace_label") or ""

    # Identity disambiguation: when the active profile is for an AGENT
    # (instance_name like "arav-codex" / "arav-claude" / "arav-gemini"),
    # the human owner sitting at the TUI shouldn't be tagged as that
    # agent in chat. Auto-default to the suffix-stripped human name
    # ("arav-codex" → "arav"). Persist on first detection so the
    # one-time work doesn't re-run every session. The user can flip
    # back to the agent identity via /identity agent later.
    #
    # Why auto and not prompt: Rich's Prompt.ask is fragile inside the
    # TUI bootstrap (VS Code terminal, Warp, etc. swallow the prompt
    # silently). Auto-default means the human always types as a human
    # by default; choosing to post as the agent is the rarer case.
    chat_identity: str = profile.get("chat_identity") or ""
    if not chat_identity:
        if _name_has_agent_keyword(agent_name):
            chat_identity = _strip_agent_suffix(agent_name)
            console.print(
                f"\n  [dim]Identity: posting as [bold success]@{chat_identity}[/]"
                f" (human). Profile instance is [muted]@{agent_name}[/]"
                f" (agent). Override with [accent]/identity agent[/].[/]"
            )
        else:
            chat_identity = agent_name
        profile["chat_identity"] = chat_identity
        try:
            ConfigManager().save(profile)
        except Exception as e:  # noqa: BLE001
            console.print(
                f"  [yellow]Could not persist chat_identity: {e} — "
                "will re-detect next session[/]"
            )

    # Exchange API key for JWT if needed
    secret = _get_auth_token(relay_url, raw_secret)

    # Initial room load — auto-create a starter room if this is a blank
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

    if state is None:
        state = HubState()
    state.set_rooms(rooms)
    if rooms:
        state.set_connected(True, "Connected")
        # Land on the room list — user navigates with Tab/Up/Down to pick a room.
        # Do NOT auto-join or pre-load messages; let the user choose intentionally.
    else:
        state.set_connected(False, "No rooms yet")

    # Start SSE push listener (primary) + slow reconciliation poller (fallback).
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

    try:
        return _main_input_loop(
            console=console,
            state=state,
            relay_url=relay_url,
            secret=secret,
            agent_name=agent_name,
            workspace_label=workspace_label,
            chat_identity=chat_identity,
        )
    finally:
        stop_event.set()
        poller.join(timeout=2)
        sse_listener.join(timeout=2)


def run_hub() -> None:
    """Outer loop: pick a profile, run a session, loop on switch."""
    console = _ui.console

    from quorus.profiles import ProfileManager
    pm = ProfileManager()
    pm.migrate_legacy_if_needed()

    try:
        while True:
            profile = pm.current_profile()
            if profile is None:
                # Fresh install — fall through to wizard, save as default.
                profile = _first_launch_setup(console)
                if not profile:
                    return
                pm.save("default", profile)
                pm.set_current("default")

            result = _run_session(profile)
            if result == "quit":
                return
            if isinstance(result, tuple) and result[0] == "switch":
                new_slug = result[1]
                try:
                    pm.set_current(new_slug)
                except FileNotFoundError:
                    console.print(
                        f"  [red]workspace {new_slug!r} vanished — "
                        "returning to current[/]"
                    )
                    continue
                # Loop continues — next iteration picks up the new profile.
                continue
            # Any other shape — treat as quit.
            return
    finally:
        console.print("\n[dim]Quorus Hub closed. Goodbye.[/dim]")
