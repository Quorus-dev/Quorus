"""Usage endpoints — per-tenant aggregate stats scoped to the caller's API key.

GET /v1/usage                 — tenant-level totals, per-room breakdown, top senders
GET /v1/usage/rooms/{room_id} — single room breakdown with locked files, goal, agents
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from murmur.auth.middleware import AuthContext, verify_auth

router = APIRouter(prefix="/v1")
_LEGACY_TENANT = "_legacy"
_PRESENCE_TIMEOUT = 120  # agent is "active" if heartbeat within 2 min
_start_time = time.time()


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.get("/usage")
async def get_usage(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Return per-tenant aggregate stats: totals, per-room breakdown, top senders.

    Response shape:
    {
        "tenant_id": str,
        "snapshot_at": ISO8601,
        "uptime_seconds": int,
        "total_sent": int,
        "total_delivered": int,
        "messages_pending": int,
        "active_agents": int,
        "rooms": [
            {"id": str, "name": str, "message_count": int,
             "members": int, "bytes_sent": int, "active_agents": int}
        ],
        "hourly_volume": [{"hour": str, "count": int}],
        "per_agent": {"name": {"sent": int, "received": int}}
    }
    """
    backends = request.app.state.backends
    analytics_svc = request.app.state.analytics_service
    room_svc = request.app.state.room_service
    tid = _tid(auth)

    # Global analytics
    stats = await analytics_svc.get_stats(tid)
    pending = await backends.messages.count_all(tid)

    # Active agents from presence
    presence_entries = await backends.presence.list_all(tid, _PRESENCE_TIMEOUT)
    active_agent_names: set[str] = {e["name"] for e in presence_entries}
    active_by_room: dict[str, list[str]] = {}
    for entry in presence_entries:
        room_ref = entry.get("room", "")
        if room_ref:
            active_by_room.setdefault(room_ref, []).append(entry["name"])

    # Per-room breakdown
    all_rooms = await room_svc.list_all(tid)
    rooms_data: list[dict] = []

    for room in all_rooms:
        rid = room["id"]
        room_name = room.get("name", rid)

        history = await backends.room_history.get_recent(tid, rid, limit=200)
        msg_count = len(history)
        bytes_sent = sum(
            len((m.get("content") or "").encode("utf-8")) for m in history
        )

        # Active agents in this room (by name or UUID)
        room_agents = (
            active_by_room.get(rid, []) + active_by_room.get(room_name, [])
        )
        members_dict = room.get("members", {})
        member_count = (
            len(members_dict)
            if isinstance(members_dict, dict)
            else len(members_dict)
        )

        rooms_data.append({
            "id": rid,
            "name": room_name,
            "message_count": msg_count,
            "members": member_count,
            "bytes_sent": bytes_sent,
            "active_agents": len(set(room_agents)),
        })

    rooms_data.sort(key=lambda r: r["message_count"], reverse=True)

    # Hourly volume
    hourly = [
        {"hour": k, "count": v}
        for k, v in sorted(stats.get("hourly_volume", {}).items())
    ]

    # Per-agent stats (sent + received)
    per_agent: dict[str, dict[str, int]] = {}
    for sender, count in stats.get("per_sender", {}).items():
        per_agent.setdefault(sender, {"sent": 0, "received": 0})
        per_agent[sender]["sent"] = count
    for recipient, count in stats.get("per_recipient", {}).items():
        per_agent.setdefault(recipient, {"sent": 0, "received": 0})
        per_agent[recipient]["received"] = count

    return {
        "tenant_id": tid,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": round(time.time() - _start_time),
        "total_sent": stats.get("total_sent", 0),
        "total_delivered": stats.get("total_delivered", 0),
        "messages_pending": pending,
        "active_agents": len(active_agent_names),
        "rooms": rooms_data,
        "hourly_volume": hourly,
        "per_agent": per_agent,
    }


@router.get("/usage/rooms/{room_id}")
async def get_room_usage(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Return usage breakdown for a specific room (includes state, agents, top senders)."""
    backends = request.app.state.backends
    room_svc = request.app.state.room_service
    tid = _tid(auth)

    # Resolve room (raises 404 if unknown)
    rid, room_data = await room_svc.get(tid, room_id)
    room_name = room_data.get("name", rid)

    # Expire stale locks, then fetch state
    await backends.room_state.expire_tasks(tid, rid)
    state = await backends.room_state.get(tid, rid)

    # Active agents from presence
    presence_entries = await backends.presence.list_all(tid, _PRESENCE_TIMEOUT)
    active_agents = sorted(
        e["name"]
        for e in presence_entries
        if e.get("room") in (rid, room_name)
    )
    if not active_agents:
        members = await backends.rooms.get_members(tid, rid)
        active_agents = sorted(members.keys())

    # Message history and per-sender counts
    history = await backends.room_history.get_recent(tid, rid, limit=200)
    sender_counts: Counter[str] = Counter()
    bytes_sent = 0
    for msg in history:
        sender = msg.get("from_name", msg.get("from", "unknown"))
        sender_counts[sender] += 1
        bytes_sent += len((msg.get("content") or "").encode("utf-8"))
    top_senders = [{"name": n, "count": c} for n, c in sender_counts.most_common(10)]

    return {
        "room_id": rid,
        "room_name": room_name,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "message_count": len(history),
        "bytes_sent": bytes_sent,
        "active_agents": active_agents,
        "locked_files": state.get("locked_files", {}),
        "active_goal": state.get("active_goal"),
        "top_senders": top_senders,
    }
