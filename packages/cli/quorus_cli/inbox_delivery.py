"""Receipts for human inbox consumers; wake daemons keep their agents' queues.

These are delivery receipts, not ordered read cursors. UUIDs and Redis stream ids
are compared for equality only. A receipt is saved only after output succeeds.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from quorus.config import resolve_config_dir
from quorus.private_files import write_private_json

SHOW_CAP = 20
SEEN_CAP = 500


def daemon_owns_queue(relay: str, participant: str) -> bool:
    """Never lease an agent's deliveries, even if its daemon is between wakes.

    The private config marker survives a daemon restart. For sessions using the
    owner's config, also consult the same daemon discovery as `quorus agent add`.
    No participant-name suffix is treated as proof of identity.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", participant):
        return True
    try:
        root = resolve_config_dir()
        if (root / ".quorus-agent").exists() or (root / "agents" / f"{participant}.json").exists():
            return True
        from quorus_cli.agent_cmd import _running_daemon_for
        return bool(_running_daemon_for(participant, relay))
    except Exception:
        return True  # unknown ownership: leave the queue alone


def _debug(category: str, exc: Exception) -> None:
    from quorus_cli.hooks import _hook_debug_log
    _hook_debug_log(category, exc)


def _cursors_path(agent: str) -> Path:
    path = resolve_config_dir() / f"cursors-{agent}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_cursors(agent: str) -> dict:
    try:
        data = json.loads(_cursors_path(agent).read_text())
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        _debug("cursor-load-fail", exc)
        return {}


def _save_cursors(agent: str, cursors: dict) -> None:
    write_private_json(_cursors_path(agent), cursors)


def delivery_id(message: dict) -> str:
    value = message.get("_delivery_id") or message.get("id") or message.get("message_id")
    return value if isinstance(value, str) else ""


def _seen_ids(cursors: dict) -> list[str]:
    raw = cursors.get("_seen")
    if isinstance(raw, list):
        return [ident for ident in raw if isinstance(ident, str)]
    # Migrate older per-room receipts without comparing UUIDs for order.
    seen = []
    for previous in cursors.values():
        values = previous if isinstance(previous, list) else [previous]
        seen.extend(ident for ident in values if isinstance(ident, str))
    return list(dict.fromkeys(seen))


def _mark_seen(cursors: dict, shown: list[dict]) -> None:
    seen = list(dict.fromkeys([*_seen_ids(cursors),
                              *(delivery_id(m) for m in shown if delivery_id(m))]))
    cursors.clear()
    cursors["_seen"] = seen[-SEEN_CAP:]


def _filter_unseen(messages: list[dict], cursors: dict) -> list[dict]:
    """Pure selection by delivery ID, across rooms and within the same batch."""
    seen = set(_seen_ids(cursors))
    fresh = []
    for message in messages:
        ident = delivery_id(message)
        if ident and ident in seen:
            continue
        fresh.append(message)
        if ident:
            seen.add(ident)
    return fresh


def receipt_key(relay: str, participant: str) -> str:
    scope = f"{relay.rstrip('/')}\n{participant}"
    return hashlib.sha256(scope.encode()).hexdigest()


@dataclass
class Batch:
    relay: str
    participant: str
    headers: dict[str, str]
    messages: list[dict]
    limit: int | None = None
    shown: list[dict] = field(init=False)

    def __post_init__(self) -> None:
        # Names may repeat on different relays; their delivery receipts must not.
        self.key = receipt_key(self.relay, self.participant)
        self.receipts = _load_cursors(self.key)
        candidate = _filter_unseen(self.messages, dict(self.receipts))
        self.shown = candidate if self.limit is None else candidate[-self.limit:]
        self.has_more = len(candidate) > len(self.shown)

    def commit(self) -> None:
        """Called AFTER successful output. Retry ACKs for previously shown ids too."""
        if daemon_owns_queue(self.relay, self.participant):
            return
        received_seen = set(_seen_ids(self.receipts))
        received_seen.update(delivery_id(m) for m in self.shown)
        try:
            _mark_seen(self.receipts, self.shown)
            _save_cursors(self.key, self.receipts)
        except OSError as exc:
            _debug("cursor-save-fail", exc)
        ids = list(dict.fromkeys(delivery_id(m) for m in self.messages
                                if delivery_id(m) and delivery_id(m) in received_seen))
        if not ids:
            return
        try:
            response = httpx.post(f"{self.relay}/messages/{self.participant}/ack",
                                  json={"delivery_ids": ids}, headers=self.headers,
                                  timeout=5, follow_redirects=False)
            response.raise_for_status()
        except Exception as exc:
            _debug("ack-exc", exc)


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


