"""Notification-delivery hooks for cross-harness AI agents.

Each external coding agent (Cursor, Gemini CLI, Codex CLI) has a different
hook surface; this module exposes one entry-point per surface so the same
underlying Quorus message stream lands in every agent's context on the next
turn. See `docs/HOOK_RESEARCH.md` for the per-harness mechanism notes.

Public functions are wired into `cli.py` as subcommands of `quorus hook`:

  quorus hook cursor-session       # Cursor sessionStart hook
  quorus hook cursor-stop          # Cursor stop hook (auto-followup)
  quorus hook gemini-beforeagent   # Gemini CLI BeforeAgent hook

Each prints ONE JSON object to stdout in the harness's expected schema
and exits 0 (or 2 if the harness expects a non-blocking error). Stderr is
reserved for diagnostics — never write notification content there.

Cursor uses additional_context at session start and followup_message at stop.
Gemini uses hookSpecificOutput.additionalContext before each turn.

Receipt files are scoped to relay and participant. Seen delivery ids survive
hook invocations; they are not ordered chat read cursors.

Diagnostics:
  Hooks must NEVER block the host harness — relay flakiness should fall
  through to a no-op return. But silent failure makes regressions invisible
  (a Codex agent breaks an export and nobody notices for hours). So every
  ``except`` here records one line to ``~/.quorus/hook-debug.log`` with
  timestamp + exception type + message. Log is rotated when it exceeds
  10 MB. Failure to write the log is itself caught — diagnostics never
  mutate exit codes.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx

from quorus_cli import inbox_delivery as _delivery
from quorus_cli.inbox_delivery import SHOW_CAP, Batch, daemon_owns_queue

# Compatibility for existing hook integrations and tests.
_cursors_path = _delivery._cursors_path
_filter_unseen = _delivery._filter_unseen
_format_for_context = _delivery._format_for_context
_load_cursors = _delivery._load_cursors
_save_cursors = _delivery._save_cursors
_mark_seen = _delivery._mark_seen
SEEN_CAP = _delivery.SEEN_CAP

# ── Diagnostics ─────────────────────────────────────────────────────────────
_HOOK_DEBUG_LOG_BYTES = 10 * 1024 * 1024  # 10 MB rotation cap


def _hook_debug_log(category: str, exc: BaseException | None, *, extra: str = "") -> None:
    """Append one structured line to ``~/.quorus/hook-debug.log``.

    Never raises. Rotates the log when it exceeds 10 MB by truncating to the
    most recent half. Used by every ``except`` branch in this module so we
    can find out *why* a hook silently no-op'd in production without
    blocking the host harness.
    """
    try:
        path = Path.home() / ".quorus" / "hook-debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotate if the log has grown past the cap.
        try:
            if path.exists() and path.stat().st_size > _HOOK_DEBUG_LOG_BYTES:
                # Keep the second half — most recent context survives.
                data = path.read_bytes()
                path.write_bytes(data[len(data) // 2:])
        except OSError:
            pass  # Rotation is best-effort; never block the host.
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if exc is not None:
            line = f"{ts} {category} {type(exc).__name__}: {exc}"
        else:
            line = f"{ts} {category}"
        if extra:
            line = f"{line} {extra}"
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Diagnostics must never crash the host. Suppress everything.
        pass


# ── Config + cursor helpers ─────────────────────────────────────────────────

# In-memory JWT cache shared across hook fires within one process. Hooks are
# typically short-lived per-invocation, but Cursor/Gemini reuse the same
# subprocess across rapid back-to-back triggers (e.g. cursor-stop right after
# a sessionStart). Caching avoids hammering /v1/auth/token on every fire.
# NEVER persisted to disk — JWTs expire and writing them would defeat that.
_JWT_CACHE: dict[str, float | str] = {}  # {"token": str, "expires_at": float-monotonic}
_JWT_REFRESH_MARGIN_S = 30  # refresh this many seconds before exp


def _load_active_profile() -> dict[str, object]:
    """Read the active workspace profile if one exists. Empty dict otherwise.

    Imported lazily so the hook code path doesn't drag profile machinery
    into every other consumer of this module.
    """
    try:
        from quorus.profiles import ProfileManager
        pm = ProfileManager()
        pm.migrate_legacy_if_needed()
        return pm.current_profile() or {}
    except Exception as exc:
        # Profile read must never crash a hook — log and return empty.
        _hook_debug_log("profile-load-fail", exc)
        return {}


def _exchange_api_key_for_jwt(relay: str, api_key: str) -> tuple[str, int]:
    """POST /v1/auth/token to swap an api_key for a short-lived JWT.

    Returns ``(token, ttl_seconds)``. Raises on network or auth failure;
    callers must catch and fall back to legacy auth.
    """
    resp = httpx.post(
        f"{relay}/v1/auth/token",
        json={"api_key": api_key},
        timeout=5,
        follow_redirects=False,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    token = data.get("token") or ""
    if not token:
        raise ValueError("token endpoint returned empty token")
    # Default to a conservative 5-min TTL if the relay omits expires_in.
    ttl = int(data.get("expires_in") or 300)
    return token, ttl


def _resolve_auth() -> tuple[str, str, dict[str, str]]:
    """Resolve (relay_url, instance, headers) for hook-side relay calls.

    Priority — fail closed, never assume legacy:
      1. Active profile api_key → exchange for JWT (in-memory cached).
      2. ``RELAY_SECRET`` env var with NO profile present → legacy fallback,
         emits a one-time stderr warning so dev-mode is observable.
      3. Otherwise → empty headers (handlers will no-op the relay call).

    Never logs the JWT or the api_key. Never persists either to disk.
    """
    # 1) Try the workspace profile first.
    profile = _load_active_profile()
    relay = (profile.get("relay_url") or "http://localhost:8080").rstrip("/")
    instance = profile.get("instance_name") or "default"
    api_key = (profile.get("api_key") or "").strip()

    if api_key:
        # Reuse cached JWT if it still has comfortable headroom.
        now = time.monotonic()
        cached_token = _JWT_CACHE.get("token")
        cached_exp = _JWT_CACHE.get("expires_at")
        if (
            isinstance(cached_token, str)
            and cached_token
            and isinstance(cached_exp, (int, float))
            and now < float(cached_exp) - _JWT_REFRESH_MARGIN_S
        ):
            return relay, instance, {"Authorization": f"Bearer {cached_token}"}
        try:
            token, ttl = _exchange_api_key_for_jwt(relay, api_key)
        except Exception as exc:
            # Relay flake or revoked key — never crash the host harness.
            # Fail closed: empty headers means the GET will 401 and the
            # hook no-ops cleanly, instead of silently downgrading auth.
            _hook_debug_log("jwt-exchange-fail", exc, extra=f"relay={relay}")
            return relay, instance, {}
        _JWT_CACHE["token"] = token
        _JWT_CACHE["expires_at"] = time.monotonic() + ttl
        return relay, instance, {"Authorization": f"Bearer {token}"}

    # 2) Legacy fallback ONLY when no profile exists. Never combine with
    #    profile path — a misconfigured profile must fail closed, not
    #    silently downgrade to admin-scope RELAY_SECRET.
    legacy_secret = os.environ.get("RELAY_SECRET", "").strip()
    if not profile and legacy_secret:
        sys.stderr.write(
            "[quorus hook] dev-mode: using RELAY_SECRET (no profile). "
            "This will fail against production where ALLOW_LEGACY_AUTH=false.\n"
        )
        # Authorization for the relay (it accepts secret-as-Bearer); the
        # X-Relay-Secret marker is a deliberate observability signal so
        # legacy traffic is distinguishable in relay logs and tests.
        return relay, instance, {
            "Authorization": f"Bearer {legacy_secret}",
            "X-Relay-Secret": legacy_secret,
        }

    # 3) No auth available — handlers will no-op rather than POST unauthed.
    _hook_debug_log("auth-unavailable", None, extra="no api_key, no relay_secret")
    return relay, instance, {}


def _reset_jwt_cache_for_tests() -> None:
    """Clear the in-memory JWT cache. Test-only — never call from prod paths."""
    _JWT_CACHE.clear()


def _config() -> tuple[str, str, str]:
    """Legacy shim — preserved so older test fixtures still patch this name.

    Production hook flow goes through ``_resolve_auth`` instead. This shim
    intentionally never reads api_key (legacy callers couldn't either) and
    returns the relay_secret as-is for the secondary string slot.
    """
    from quorus.config import load_config
    cfg = load_config()
    return (
        cfg.get("relay_url", "http://localhost:8080").rstrip("/"),
        cfg.get("relay_secret", ""),
        cfg.get("instance_name", "default"),
    )


def _fetch_unread(
    relay: str,
    headers_or_secret: dict[str, str] | str,
    agent: str,
) -> list[dict]:
    """Pull pending messages for *agent* from the relay's inbox endpoint.

    Human consumers peek then lease with `ack=manual`. Agent identities never
    fetch: even an unacked lease would hide deliveries from reflexd for 60 s.

    Accepts either a pre-built headers dict (preferred — what
    ``_resolve_auth`` returns) or a legacy positional secret string for
    backward compatibility with older test fixtures. Either way, never
    log the bearer value.

    All exceptions are caught and logged to ``~/.quorus/hook-debug.log``;
    the function returns ``[]`` on failure. The host harness must never
    be blocked by relay flakiness, but the silent-failure mode that hid
    the cold-install-smoke regression must never recur.
    """
    if daemon_owns_queue(relay, agent):
        return []
    if isinstance(headers_or_secret, dict):
        headers = headers_or_secret
    elif headers_or_secret:
        headers = {"Authorization": f"Bearer {headers_or_secret}"}
    else:
        headers = {}
    try:
        peek = httpx.get(
            f"{relay}/messages/{agent}/peek",
            headers=headers,
            timeout=3,
            follow_redirects=True,
        )
        if peek.status_code != 200:
            _hook_debug_log(
                "fetch-peek-non200",
                None,
                extra=f"relay={relay} agent={agent} status={peek.status_code}",
            )
            return []
        counts = peek.json() or {}
        if not (counts.get("count", 0) or counts.get("pending", 0)):
            return []
        full = httpx.get(
            f"{relay}/messages/{agent}",
            params={"ack": "manual"},
            headers=headers,
            timeout=3,
            follow_redirects=True,
        )
        if full.status_code != 200:
            _hook_debug_log(
                "fetch-full-non200",
                None,
                extra=f"relay={relay} agent={agent} status={full.status_code}",
            )
            return []
        return (full.json() or {}).get("messages", []) or []
    except Exception as exc:
        # Never block the host harness on relay flakiness — but log so a
        # broken relay doesn't go undetected for days.
        _hook_debug_log("fetch-unread-exc", exc, extra=f"relay={relay} agent={agent}")
        return []


# ── Per-harness entrypoints ─────────────────────────────────────────────────


def handle_cursor_session() -> int:
    """Cursor sessionStart hook — emits {additional_context: "..."}.

    Fires at composer open or `/clear` to inject unread human-inbox messages.
    """
    try:
        relay, instance, headers = _resolve_auth()
        msgs = _fetch_unread(relay, headers, instance)
        if not msgs:
            print(json.dumps({"additional_context": ""}))
            return 0
        batch = Batch(relay, instance, headers, msgs, limit=SHOW_CAP)
        fresh = batch.shown
        print(json.dumps({"additional_context": _format_for_context(fresh)}), flush=True)
        batch.commit()
        return 0
    except Exception as exc:
        _hook_debug_log(
            "cursor-session-exc", exc,
            extra=traceback.format_exc(limit=2).replace("\n", " | "),
        )
        # Cursor expects valid JSON even on failure — emit a safe no-op.
        print(json.dumps({"additional_context": ""}))
        return 0


def handle_cursor_stop() -> int:
    """Cursor stop hook — emits {followup_message: "..."} OR empty.

    Fires when the agent loop ends. If new Quorus messages have arrived
    since session start, push them as a follow-up so they show up as the
    next user message — the only way to push to Cursor mid-session.
    """
    try:
        relay, instance, headers = _resolve_auth()
        sentinel = Path.home() / ".quorus" / f"pending-{instance}.flag"
        has_pending = sentinel.exists()
        msgs = _fetch_unread(relay, headers, instance) if has_pending else []
        if not msgs:
            print(json.dumps({}))
            return 0
        batch = Batch(relay, instance, headers, msgs, limit=SHOW_CAP)
        fresh = batch.shown
        if not batch.has_more:
            try:
                sentinel.unlink()
            except OSError as exc:
                _hook_debug_log("cursor-sentinel-unlink-fail", exc, extra=str(sentinel))
        if not fresh:
            batch.commit()
            print(json.dumps({}))
            return 0
        print(json.dumps({"followup_message": _format_for_context(fresh)}), flush=True)
        batch.commit()
        return 0
    except Exception as exc:
        _hook_debug_log(
            "cursor-stop-exc", exc,
            extra=traceback.format_exc(limit=2).replace("\n", " | "),
        )
        print(json.dumps({}))
        return 0


def handle_gemini_beforeagent() -> int:
    """Gemini CLI BeforeAgent hook — emits hookSpecificOutput.additionalContext.

    Fires before every model turn. Cleanest of the three surfaces — the
    additionalContext is appended directly to the user's prompt for the
    current turn. No tricks required.
    """
    try:
        relay, instance, headers = _resolve_auth()
        msgs = _fetch_unread(relay, headers, instance)
        if not msgs:
            print(json.dumps({"hookSpecificOutput": {"additionalContext": ""}}))
            return 0
        batch = Batch(relay, instance, headers, msgs, limit=SHOW_CAP)
        fresh = batch.shown
        if not fresh:
            batch.commit()
            print(json.dumps({"hookSpecificOutput": {"additionalContext": ""}}))
            return 0
        print(json.dumps({
            "hookSpecificOutput": {"additionalContext": _format_for_context(fresh)}
        }), flush=True)
        batch.commit()
        return 0
    except Exception as exc:
        _hook_debug_log(
            "gemini-beforeagent-exc", exc,
            extra=traceback.format_exc(limit=2).replace("\n", " | "),
        )
        # Gemini expects valid JSON even on failure — emit a safe no-op.
        print(json.dumps({"hookSpecificOutput": {"additionalContext": ""}}))
        return 0



def handle_claude_session_start() -> int:
    """Claude Code SessionStart — register this session as live (L1).

    Records pid/cwd (never the messaging token) so reflexd can tell "a human has
    a session open on this repo" and defer to it instead of cold-spawning a
    competing headless agent. Emits ``{}`` so the hook never alters the
    session's context.
    """
    try:
        from quorus_cli import live_sessions as _ls

        instance = ""
        try:
            _relay, instance, _headers = _resolve_auth()
        except Exception:
            pass
        _ls.register(
            pid=os.getppid(),
            cwd=os.getcwd(),
            participant=instance,
            has_socket=bool(os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")),
        )
    except Exception as exc:
        _hook_debug_log("claude-session-start-exc", exc)
    print(json.dumps({}))
    return 0


def handle_claude_session_end() -> int:
    """Claude Code SessionEnd — drop this session from the live registry."""
    try:
        from quorus_cli import live_sessions as _ls

        _ls.unregister(os.getppid())
    except Exception as exc:
        _hook_debug_log("claude-session-end-exc", exc)
    print(json.dumps({}))
    return 0


def handle_claude_stop() -> int:
    """Claude Code Stop: emit human-inbox messages unless stop_hook_active is set.

    Agent queues belong to reflexd; no hook leases or acknowledges them.
    """
    try:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except (json.JSONDecodeError, ValueError):
            payload = {}
        if payload.get("stop_hook_active"):
            print(json.dumps({}))
            return 0

        relay, instance, headers = _resolve_auth()
        msgs = _fetch_unread(relay, headers, instance)
        if not msgs:
            print(json.dumps({}))
            return 0
        batch = Batch(relay, instance, headers, msgs, limit=SHOW_CAP)
        fresh = batch.shown
        if not fresh:
            batch.commit()
            print(json.dumps({}))
            return 0
        print(json.dumps({
            "decision": "block",
            "reason": (
                "New Quorus room messages arrived while you worked. Read them "
                "and reply in the room with `quorus say <room> \"...\"` "
                "(or the quorus MCP tools) before stopping:\n\n"
                + _format_for_context(fresh)
            ),
        }), flush=True)
        batch.commit()
        return 0
    except Exception as exc:
        _hook_debug_log(
            "claude-stop-exc", exc,
            extra=traceback.format_exc(limit=2).replace("\n", " | "),
        )
        print(json.dumps({}))
        return 0


# Convenience dispatcher used by `_cmd_hook` in cli.py.
HOOK_HANDLERS = {
    "cursor-session": handle_cursor_session,
    "cursor-stop": handle_cursor_stop,
    "gemini-beforeagent": handle_gemini_beforeagent,
    "claude-session-start": handle_claude_session_start,
    "claude-session-end": handle_claude_session_end,
    "claude-stop": handle_claude_stop,
}


def dispatch(name: str) -> int:
    handler = HOOK_HANDLERS.get(name)
    if handler is None:
        sys.stderr.write(f"unknown hook: {name}\n")
        return 2
    return handler()
