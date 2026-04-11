"""Analytics route handler."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from murmur.auth.middleware import AuthContext, verify_auth

router = APIRouter()
_LEGACY_TENANT = "_legacy"
_start_time = time.time()


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def reset_analytics() -> None:
    """Reset the uptime clock — called by reset_state()."""
    global _start_time
    _start_time = time.time()


@router.get("/analytics")
async def get_analytics(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    analytics_svc = request.app.state.analytics_service
    stats = await analytics_svc.get_stats(_tid(auth))

    # Build per_participant from per_sender/per_recipient
    per_participant: dict[str, dict[str, int]] = {}
    for sender, count in stats.get("per_sender", {}).items():
        per_participant.setdefault(sender, {"sent": 0, "received": 0})
        per_participant[sender]["sent"] = count
    for recipient, count in stats.get("per_recipient", {}).items():
        per_participant.setdefault(recipient, {"sent": 0, "received": 0})
        per_participant[recipient]["received"] = count

    # Count pending messages via the backend
    backends = request.app.state.backends
    pending = await backends.messages.count_all(_tid(auth))

    # Hourly volume from stats if available
    hourly = [
        {"hour": k, "count": v}
        for k, v in sorted(stats.get("hourly_volume", {}).items())
    ]

    return {
        "total_messages_sent": stats.get("total_sent", 0),
        "total_messages_delivered": stats.get("total_delivered", 0),
        "messages_pending": pending,
        "participants": per_participant,
        "hourly_volume": hourly,
        "uptime_seconds": round(time.time() - _start_time),
    }
