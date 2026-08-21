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
import time
import uuid
from collections import OrderedDict
from typing import Any

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


def _preview(value: Any) -> str:
    """Short, single-line, length-capped rendering of a tool input."""
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        text = "<unrenderable>"
    text = " ".join(text.split())
    if len(text) > PREVIEW_CHARS:
        text = text[:PREVIEW_CHARS] + "…"
    return text


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
        while len(self._requests) > MAX_REQUESTS:
            self._requests.popitem(last=False)

    async def create(
        self,
        tid: str,
        *,
        room: str,
        agent: str,
        tool_name: str,
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
                "agent": agent,
                "tool_name": tool_name,
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
