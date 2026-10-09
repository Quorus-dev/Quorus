"""Claude Code channel push — room messages appear live in an open session.

Contract (code.claude.com/docs/en/channels-reference, verified 2026-10-08):

* capability ``experimental["claude/channel"] = {}`` — exactly ``{}``;
* notification ``notifications/claude/channel`` with params
  ``{"content": str, "meta": {identifier: str}}``; meta keys must be
  letters/digits/underscores or Claude Code silently drops them;
* Claude Code registers the listener only when launched interactively with
  ``--dangerously-load-development-channels server:quorus`` (research
  preview; custom servers are not on the allowlist).

Before this module the server sent ``{"message", "channel"}`` params, which
Claude Code dropped without error — so no Quorus message ever reached an
open Claude window, which is why "notifications never worked".
"""

from __future__ import annotations

import os
import re
from typing import Any

CHANNEL_METHOD = "notifications/claude/channel"

# What reaches the session: "mentions" (DMs + messages that @-mention this
# identity — the default, so room chatter doesn't burn turns), "all", "off".
PUSH_MODE = (os.environ.get("QUORUS_CHANNEL_PUSH") or "mentions").strip().lower()

_PUSHABLE_TYPES = {"chat", "request", "question", "dm", "direct"}

CHANNEL_INSTRUCTIONS = (
    "Live room messages: Quorus messages addressed to you arrive as "
    '<channel source="quorus" sender="..." room="..." message_id="...">. '
    "Treat them like a teammate talking to you: if one asks for work, do it; "
    "answer in the same room with send_room_message(room_id=<room>, ...), or "
    "with send_message(to=<sender>, ...) when there is no room. Do not reply "
    "just to acknowledge."
)


def _mentions(content: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w-])@{re.escape(name)}(?![\w-])", content))


def should_push(msg: dict[str, Any], instance: str, mode: str = PUSH_MODE) -> bool:
    """Decide whether *msg* is delivered into the open session."""
    if mode == "off" or not isinstance(msg, dict):
        return False
    sender = msg.get("from_name") or msg.get("from") or ""
    if sender == instance:
        return False  # never echo our own posts back into the session
    if (msg.get("message_type") or "chat") not in _PUSHABLE_TYPES:
        return False  # wake_intent, social verbs, system events
    if mode == "all":
        return True
    content = msg.get("content") or ""
    is_dm = not msg.get("room")
    return is_dm or _mentions(content, instance)


def channel_params(msg: dict[str, Any]) -> dict[str, Any]:
    """Build spec-shaped params: ``content`` plus identifier-only ``meta``."""
    meta = {
        "sender": str(msg.get("from_name") or msg.get("from") or "unknown"),
        "room": str(msg.get("room") or ""),
        "message_id": str(msg.get("message_id") or msg.get("id") or ""),
    }
    return {
        "content": str(msg.get("content") or ""),
        "meta": {k: v for k, v in meta.items() if v},
    }
