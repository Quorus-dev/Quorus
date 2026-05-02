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

Cursor's quirks:
  - `beforeSubmitPrompt` silently strips `updated_input` (confirmed bug),
    so we cannot inject mid-conversation context that way.
  - `sessionStart` returns `additional_context`.
  - `stop` returns `followup_message` which auto-submits — the only way
    to push a Quorus message into Cursor mid-session.

Gemini's `BeforeAgent` hook is purpose-built for this: returns
`hookSpecificOutput.additionalContext`, appended to the user's prompt
for the current turn. No tricks needed.

Cursor state file: ~/.quorus/cursors-<agent>.json — last-seen message id
per (room, agent). Survives across machines if synced.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx


def _config() -> tuple[str, str, str]:
    """Resolve relay_url, secret, instance from quorus config."""
    from quorus.config import load_config
    cfg = load_config()
    return (
        cfg.get("relay_url", "http://localhost:8080").rstrip("/"),
        cfg.get("relay_secret", ""),
        cfg.get("instance_name", "default"),
    )


def _cursors_path(agent: str) -> Path:
    """Per-agent cursor file. Tracks last-seen message id per room."""
    p = Path.home() / ".quorus" / f"cursors-{agent}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_cursors(agent: str) -> dict[str, str]:
    p = _cursors_path(agent)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cursors(agent: str, cursors: dict[str, str]) -> None:
    p = _cursors_path(agent)
    try:
        p.write_text(json.dumps(cursors, indent=2))
    except OSError:
        pass  # Best effort — don't block agent on disk failure


def _fetch_unread(relay: str, secret: str, agent: str) -> list[dict]:
    """Pull pending messages for *agent* from the relay's inbox endpoint.

    Mirrors `_cmd_inbox` in cli.py: peek first to avoid pulling when empty,
    then GET /messages/{agent} with `ack=manual` so the relay doesn't drop
    them off the queue (we want each harness to see them once each).
    """
    try:
        peek = httpx.get(
            f"{relay}/messages/{agent}/peek",
            headers={"Authorization": f"Bearer {secret}"} if secret else {},
            timeout=3,
            follow_redirects=True,
        )
        if peek.status_code != 200:
            return []
        if (peek.json() or {}).get("count", 0) == 0:
            return []
        full = httpx.get(
            f"{relay}/messages/{agent}",
            params={"ack": "manual"},
            headers={"Authorization": f"Bearer {secret}"} if secret else {},
            timeout=3,
            follow_redirects=True,
        )
        if full.status_code != 200:
            return []
        return (full.json() or {}).get("messages", []) or []
    except Exception:
        return []  # Never block the host harness on relay flakiness


def _format_for_context(messages: list[dict]) -> str:
    """Render messages as a compact context block the LLM can read."""
    if not messages:
        return ""
    lines = ["[Quorus] new messages while you were idle:"]
    for m in messages[-20:]:  # cap at 20 to avoid blowing context budgets
        sender = m.get("from_name") or m.get("sender") or "?"
        room = m.get("room") or ""
        room_part = f" #{room}" if room else ""
        body = (m.get("content") or "").strip().replace("\n", " ")
        if len(body) > 280:
            body = body[:277] + "..."
        ts = (m.get("timestamp") or "")[:19].replace("T", " ")
        lines.append(f"  [{ts}] @{sender}{room_part}: {body}")
    lines.append("")
    lines.append("Reply via mcp__quorus__send_room_message or `quorus say`.")
    return "\n".join(lines)


def _filter_unseen(messages: list[dict], cursors: dict[str, str]) -> list[dict]:
    """Filter to messages newer than per-room cursors. Updates cursors in place."""
    fresh = []
    for m in messages:
        room = m.get("room") or "_dm"
        msg_id = m.get("id") or m.get("message_id") or ""
        last_seen = cursors.get(room, "")
        # Lex compare on ULID/UUID timestamps works for ULID; for UUIDv4
        # the "last seen" semantic falls back to "any new message id" —
        # acceptable since the relay deduplicates server-side.
        if not last_seen or msg_id > last_seen or msg_id != last_seen:
            fresh.append(m)
            if msg_id:
                cursors[room] = max(cursors.get(room, ""), msg_id)
    return fresh


# ── Per-harness entrypoints ─────────────────────────────────────────────────


def handle_cursor_session() -> int:
    """Cursor sessionStart hook — emits {additional_context: "..."}.

    Fires once at composer open / `/clear`. Inject all unread Quorus
    messages so the agent picks up where the team left off.
    """
    relay, secret, instance = _config()
    msgs = _fetch_unread(relay, secret, instance)
    if not msgs:
        print(json.dumps({"additional_context": ""}))
        return 0
    cursors = _load_cursors(instance)
    fresh = _filter_unseen(msgs, cursors)
    _save_cursors(instance, cursors)
    print(json.dumps({"additional_context": _format_for_context(fresh)}))
    return 0


def handle_cursor_stop() -> int:
    """Cursor stop hook — emits {followup_message: "..."} OR empty.

    Fires when the agent loop ends. If new Quorus messages have arrived
    since session start, push them as a follow-up so they show up as the
    next user message — the only way to push to Cursor mid-session.
    """
    relay, secret, instance = _config()
    sentinel = Path.home() / ".quorus" / f"pending-{instance}.flag"
    has_pending = sentinel.exists()
    msgs = _fetch_unread(relay, secret, instance) if has_pending else []
    if not msgs:
        # Empty stop hook output — Cursor proceeds normally.
        print(json.dumps({}))
        return 0
    cursors = _load_cursors(instance)
    fresh = _filter_unseen(msgs, cursors)
    _save_cursors(instance, cursors)
    try:
        sentinel.unlink()
    except OSError:
        pass
    if not fresh:
        print(json.dumps({}))
        return 0
    print(json.dumps({"followup_message": _format_for_context(fresh)}))
    return 0


def handle_gemini_beforeagent() -> int:
    """Gemini CLI BeforeAgent hook — emits hookSpecificOutput.additionalContext.

    Fires before every model turn. Cleanest of the three surfaces — the
    additionalContext is appended directly to the user's prompt for the
    current turn. No tricks required.
    """
    relay, secret, instance = _config()
    msgs = _fetch_unread(relay, secret, instance)
    if not msgs:
        print(json.dumps({"hookSpecificOutput": {"additionalContext": ""}}))
        return 0
    cursors = _load_cursors(instance)
    fresh = _filter_unseen(msgs, cursors)
    _save_cursors(instance, cursors)
    if not fresh:
        print(json.dumps({"hookSpecificOutput": {"additionalContext": ""}}))
        return 0
    print(json.dumps({
        "hookSpecificOutput": {"additionalContext": _format_for_context(fresh)}
    }))
    return 0


# Convenience dispatcher used by `_cmd_hook` in cli.py.
HOOK_HANDLERS = {
    "cursor-session": handle_cursor_session,
    "cursor-stop": handle_cursor_stop,
    "gemini-beforeagent": handle_gemini_beforeagent,
}


def dispatch(name: str) -> int:
    handler = HOOK_HANDLERS.get(name)
    if handler is None:
        sys.stderr.write(f"unknown hook: {name}\n")
        return 2
    return handler()
