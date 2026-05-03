"""Stream B helpers for reflexd — pulled out of reflexd.py to keep it under
its LoC budget. All pure functions; the daemon imports them and uses them
inline. No I/O happens here other than what's delegated to
:mod:`quorus.runtime.memory` (which the caller imports separately).

Three responsibilities:

1. Render a "RECENT MEMORY" block from a list of memory entries.
2. Summarise a wake → reply pair into a 1-sentence memory line.
3. Resolve a wake envelope's effective ``thread_root_id``.

Each function is tiny and side-effect-free. The contract tests in
``tests/test_reflexd_defer_announce.py`` exercise them directly because
they are too small to need a daemon-level integration test.
"""
from __future__ import annotations

from typing import Any

# Cap how many past memory entries we drop into the wake prompt. The
# memory module also caps to 5KB on disk; this is a second guard so the
# prompt stays small even if a future bump raises the file cap.
MEMORY_CONTEXT_LIMIT = 10

# Mirrors reflexd's preview-cap so summaries fit on a single chat line.
_PREVIEW_CHARS = 80


def _safe_preview(content: str) -> str:
    """Single-line, length-capped preview of a chat message body."""
    if not content:
        return ""
    flat = content.replace("\n", " ").replace("\r", " ")
    if len(flat) <= _PREVIEW_CHARS:
        return flat
    return flat[: _PREVIEW_CHARS - 1] + "…"


def render_memory_context(entries: list[dict[str, Any]]) -> str:
    """Render recent memory entries as a "RECENT MEMORY" prompt section.

    Returns an empty string if *entries* is empty, so callers can
    concatenate without a conditional. Entries are expected oldest-first
    (the order :func:`quorus.runtime.memory.read_recent` returns).
    """
    if not entries:
        return ""
    lines = ["# RECENT MEMORY (oldest → newest)"]
    for entry in entries:
        ts = (entry.get("ts") or "")[:19]  # YYYY-MM-DDTHH:MM:SS
        room = entry.get("room") or "?"
        summary = entry.get("summary") or ""
        lines.append(f"  [{ts}] (#{room}) {summary}")
    return "\n".join(lines) + "\n\n"


def summarise_reply_for_memory(
    *,
    envelope: dict[str, Any],
    reply_text: str,
    triage: Any | None = None,
) -> str:
    """Distill one wake → reply into a 1-sentence memory-line.

    The summary mentions: who triggered the wake, what kind of triage
    the daemon classified, and the first ~80 chars of the reply. Full
    replies are too long for the 5KB cap; the summary is enough to
    re-establish context on the next wake.
    """
    sender = envelope.get("from_name") or "?"
    kind = getattr(triage, "kind", None) if triage is not None else None
    snippet = _safe_preview(reply_text)[:120]
    parts = [f"replied to @{sender}"]
    if kind:
        parts.append(f"({kind})")
    if snippet:
        parts.append(f"— {snippet}")
    return " ".join(parts)


def envelope_thread_root(envelope: dict[str, Any]) -> str | None:
    """Return the thread_root_id of *envelope*, or None.

    Resolution rules — same as the relay endpoint:
      1. Explicit ``thread_root_id``.
      2. Else fall back to ``id`` (the parent IS the root if it has no root).
      3. Else None.

    Reflexd uses this so its reply inherits the thread of the message that
    triggered the wake — multi-turn debates stay grouped in the TUI.
    """
    explicit = envelope.get("thread_root_id")
    if explicit:
        return str(explicit)
    parent_id = envelope.get("id")
    if parent_id:
        return str(parent_id)
    return None


__all__ = [
    "MEMORY_CONTEXT_LIMIT",
    "envelope_thread_root",
    "render_memory_context",
    "summarise_reply_for_memory",
]
