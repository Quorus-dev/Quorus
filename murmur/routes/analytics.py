"""Analytics route handler."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from murmur.auth.middleware import AuthContext, verify_auth

router = APIRouter()
_LEGACY_TENANT = "_legacy"

# Module-level analytics state for backward compat
_analytics: dict = {
    "total_sent": 0,
    "total_delivered": 0,
    "per_participant": {},
    "hourly_volume": {},
}
_start_time = time.time()


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def track_send(sender: str) -> None:
    """Track a message send in the analytics state."""
    _analytics["total_sent"] += 1
    if sender not in _analytics["per_participant"]:
        _analytics["per_participant"][sender] = {"sent": 0, "received": 0}
    _analytics["per_participant"][sender]["sent"] += 1
    hour_key = (
        datetime.now(timezone.utc)
        .replace(minute=0, second=0, microsecond=0)
        .isoformat()
    )
    _analytics["hourly_volume"][hour_key] = (
        _analytics["hourly_volume"].get(hour_key, 0) + 1
    )


def track_delivery(recipient: str, count: int) -> None:
    """Track message delivery in the analytics state."""
    if count == 0:
        return
    _analytics["total_delivered"] += count
    if recipient not in _analytics["per_participant"]:
        _analytics["per_participant"][recipient] = {"sent": 0, "received": 0}
    _analytics["per_participant"][recipient]["received"] += count


def reset_analytics() -> None:
    """Reset analytics state — called by reset_state()."""
    global _start_time
    _analytics["total_sent"] = 0
    _analytics["total_delivered"] = 0
    _analytics["per_participant"].clear()
    _analytics["hourly_volume"].clear()
    _start_time = time.time()


def _prune_hourly_volume() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    _analytics["hourly_volume"] = {
        k: v for k, v in _analytics["hourly_volume"].items() if k >= cutoff
    }


def _count_pending_messages(backends) -> int:
    """Count pending logical messages across all queues."""
    total = 0
    chunk_groups: dict[str, set] = defaultdict(set)
    for key, msgs in backends.messages._queues.items():
        for msg in msgs:
            if "chunk_group" in msg:
                chunk_groups[msg["chunk_group"]].add(msg["id"])
            else:
                total += 1
    # Each complete or incomplete chunk group counts as one logical message
    total += len(chunk_groups)
    return total


@router.get("/analytics")
async def get_analytics(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    _prune_hourly_volume()
    backends = request.app.state.backends
    pending = _count_pending_messages(backends)
    hourly = [
        {"hour": k, "count": v}
        for k, v in sorted(_analytics["hourly_volume"].items())
    ]
    return {
        "total_messages_sent": _analytics["total_sent"],
        "total_messages_delivered": _analytics["total_delivered"],
        "messages_pending": pending,
        "participants": _analytics["per_participant"],
        "hourly_volume": hourly,
        "uptime_seconds": round(time.time() - _start_time),
    }
