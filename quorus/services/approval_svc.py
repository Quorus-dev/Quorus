"""Human-in-the-loop approvals for agent tool calls (WAKE_REBUILD L3).

A headless agent that hits a permission prompt has nobody to answer it: the
run stalls invisibly until the wall-clock kill, and the room sees silence.
This service turns that prompt into a room event a human can answer from
anywhere — including a different machine than the agent.

Flow:
  1. The woken harness calls the ``approve`` MCP tool (wired as Claude
     Code's ``--permission-prompt-tool``), which POSTs a request here.
  2. The relay broadcasts it to the room; a human runs ``quorus approve
     <id>`` / ``quorus deny <id>`` (or the TUI equivalent).
  3. The MCP tool polls until decided, then returns allow/deny to the
     harness in its expected shape.

Requests are tenant-scoped, LRU+TTL bounded, and never store tool INPUT
verbatim beyond a redacted preview — approval prompts routinely carry file
contents and command lines, and this record is readable by every room
member.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict
from typing import Any

# Any participant whose name carries a harness suffix is an AGENT. Agents
# may request approval; they may never grant one — a gate an agent can open
# is not a gate.
AGENT_NAME_RE = re.compile(
    r"-(claude|codex|gemini|cursor|opencode|cline)(-|$)", re.IGNORECASE,
)

# Obvious secret shapes, redacted before a preview is broadcast to a room
# and written into persistent history.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(bearer)\s+\S+"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|APIKEY|API_KEY)[A-Z0-9_]*)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\b(--(?:token|password|api-key|secret))[= ]\S+"),
    re.compile(r"\b(sk-[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|quorus_sk_\S+|mct_\S+)"),
)

# Per-TENANT cap. A single global cap let one busy tenant silently evict
# another tenant's live requests (and a looping agent DoS everyone).
MAX_REQUESTS_PER_TENANT = 200
MAX_REQUESTS = 500
DEFAULT_TTL_S = 300
PREVIEW_CHARS = 200

PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"
EXPIRED = "expired"


class ApprovalError(Exception):
    """Bad approval request or decision."""


def _now() -> float:
    return time.time()


def redact(text: str) -> str:
    """Mask obvious credentials before a preview leaves the process."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    return text


def _preview(value: Any) -> str:
    """Redacted, single-line, length-capped rendering of a tool input.

    The full input is NEVER stored (this record is broadcast to a room and
    written into persistent history). When truncation happens the true
    length is shown, so a human can see that a "git status" is actually a
    5,000-character command and refuse it.
    """
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        text = "<unrenderable>"
    text = " ".join(text.split())
    text = redact(text)
    if len(text) > PREVIEW_CHARS:
        text = f"{text[:PREVIEW_CHARS]}… [truncated, {len(text)} chars total]"
    return text


def input_digest(value: Any) -> str:
    """Stable hash of the exact input under review.

    The human approves a PREVIEW; the harness executes the full input. The
    bridge re-hashes what it is about to run and refuses unless it matches
    this digest, so a padded or swapped payload cannot ride an approval
    granted for something else.
    """
    try:
        canonical = json.dumps(value, sort_keys=True, default=repr)
    except Exception:
        canonical = repr(value)
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()


def _safe_tool_name(name: str) -> str:
    """Tool names are identifiers, not prose: strip anything that could
    forge a line in the room notice."""
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "", str(name or ""))
    return cleaned[:60] or "unknown"


def is_agent_name(name: str | None) -> bool:
    return bool(name and AGENT_NAME_RE.search(name))


class ApprovalSvc:
    """In-memory approval registry, scoped per tenant."""

    def __init__(self) -> None:
        # request_id -> record
        self._requests: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _expire(self) -> None:
        now = _now()
        for rid, rec in list(self._requests.items()):
            if rec["status"] == PENDING and rec["expires_at"] < now:
                rec["status"] = EXPIRED
        # Evict SETTLED records only, oldest first, and cap per tenant so a
        # busy (or hostile) tenant cannot silently delete another tenant's
        # live request out from under a waiting agent.
        settled = [
            k for k, v in self._requests.items() if v["status"] != PENDING
        ]
        while len(self._requests) > MAX_REQUESTS and settled:
            self._requests.pop(settled.pop(0), None)
        by_tenant: dict[str, list[str]] = {}
        for k, v in self._requests.items():
            by_tenant.setdefault(v["tenant_id"], []).append(k)
        for keys in by_tenant.values():
            over = len(keys) - MAX_REQUESTS_PER_TENANT
            for k in keys[:max(0, over)]:
                if self._requests.get(k, {}).get("status") != PENDING:
                    self._requests.pop(k, None)

    async def create(
        self,
        tid: str,
        *,
        room: str,
        agent: str,
        tool_name: str,
        room_id: str = "",
        tool_input: Any = None,
        ttl_seconds: int = DEFAULT_TTL_S,
    ) -> dict[str, Any]:
        if not tool_name.strip():
            raise ApprovalError("tool_name required")
        if not agent.strip():
            raise ApprovalError("agent required")
        ttl = max(10, min(int(ttl_seconds), 3600))
        async with self._lock:
            self._expire()
            rid = f"apr_{uuid.uuid4().hex[:12]}"
            rec = {
                "id": rid,
                "tenant_id": tid,
                "room": room,
                # Canonical id for membership checks; ``room`` is the
                # display name and does not resolve in get_members().
                "room_id": room_id or room,
                "agent": agent,
                # Sanitized: an unfiltered tool_name let an agent inject
                # forged "approve with `quorus approve <other-id>`" lines
                # into the room notice.
                "tool_name": _safe_tool_name(tool_name),
                "input_digest": input_digest(tool_input),
                # Preview only — never the raw input (may hold file bodies,
                # command lines, or secrets) since every room member can read it.
                "input_preview": _preview(tool_input),
                "status": PENDING,
                "decided_by": None,
                "reason": "",
                "created_at": _now(),
                "expires_at": _now() + ttl,
            }
            self._requests[rid] = rec
            return dict(rec)

    async def get(self, tid: str, rid: str) -> dict[str, Any] | None:
        async with self._lock:
            self._expire()
            rec = self._requests.get(rid)
            if rec is None or rec["tenant_id"] != tid:
                return None
            return dict(rec)

    async def decide(
        self,
        tid: str,
        rid: str,
        *,
        approve: bool,
        decided_by: str,
        reason: str = "",
    ) -> dict[str, Any]:
        async with self._lock:
            self._expire()
            rec = self._requests.get(rid)
            if rec is None or rec["tenant_id"] != tid:
                raise ApprovalError("unknown approval request")
            if rec["status"] != PENDING:
                # Idempotent: re-deciding returns the settled record rather
                # than flipping a decision an agent may already have acted on.
                return dict(rec)
            rec["status"] = APPROVED if approve else DENIED
            rec["decided_by"] = decided_by
            rec["reason"] = reason[:500]
            rec["decided_at"] = _now()
            return dict(rec)

    async def list_pending(
        self, tid: str, *, room: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire()
            out = [
                dict(r) for r in self._requests.values()
                if r["tenant_id"] == tid and r["status"] == PENDING
                and (room is None or r["room"] == room)
            ]
        out.sort(key=lambda r: r["created_at"])
        return out

    async def reset(self, tid: str | None = None) -> None:
        async with self._lock:
            if tid is None:
                self._requests.clear()
                return
            self._requests = OrderedDict(
                (k, v) for k, v in self._requests.items()
                if v["tenant_id"] != tid
            )
