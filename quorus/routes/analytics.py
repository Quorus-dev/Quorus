"""Analytics route handler."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from quorus.auth.middleware import AuthContext, verify_auth

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
    """Tenant analytics dashboard.

    S2 — scope guard: per-participant {sent,received} previously included
    EVERY participant in the tenant. We now restrict it to participants
    who appear in the caller's visible rooms — admins/legacy still see
    all participants. Mirrors the /v1/usage authorization pattern.
    """
    analytics_svc = request.app.state.analytics_service
    tid = _tid(auth)
    stats = await analytics_svc.get_stats(tid)

    # Determine the participant allow-list for this caller.
    is_admin = auth.role == "admin"
    visible_participants: set[str] | None = None
    if not (auth.is_legacy or is_admin):
        room_svc = getattr(request.app.state, "room_service", None)
        if room_svc is not None:
            visible_participants = set()
            visible_rooms = await room_svc.list_for_member(tid, auth.sub or "")
            for room in visible_rooms:
                raw = room.get("members") or {}
                names = list(raw.keys()) if isinstance(raw, dict) else list(raw)
                visible_participants.update(names)
            # Always include the caller themself.
            if auth.sub:
                visible_participants.add(auth.sub)

    def _allowed(name: str) -> bool:
        return visible_participants is None or name in visible_participants

    # Build per_participant from per_sender/per_recipient, scoped.
    per_participant: dict[str, dict[str, int]] = {}
    for sender, count in stats.get("per_sender", {}).items():
        if not _allowed(sender):
            continue
        per_participant.setdefault(sender, {"sent": 0, "received": 0})
        per_participant[sender]["sent"] = count
    for recipient, count in stats.get("per_recipient", {}).items():
        if not _allowed(recipient):
            continue
        per_participant.setdefault(recipient, {"sent": 0, "received": 0})
        per_participant[recipient]["received"] = count

    # Count pending messages via the backend
    backends = request.app.state.backends
    pending = await backends.messages.count_all(tid)

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
