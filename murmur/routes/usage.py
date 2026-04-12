"""Usage endpoints — per-tenant aggregate stats scoped to the caller's API key.

GET /v1/usage                 — tenant-level totals, per-room breakdown
GET /v1/usage/rooms/{room_id} — single room breakdown with locked files, goal, agents

Authorization:
- /v1/usage: Admins see all tenant rooms; regular users see only rooms they're members of
- /v1/usage/rooms/{room_id}: Requires room membership
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.room_auth import require_room_member

router = APIRouter(prefix="/v1")
_LEGACY_TENANT = "_legacy"
_PRESENCE_TIMEOUT = 120  # agent is "active" if heartbeat within 2 min


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.get("/usage")
async def get_usage(
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Return per-tenant aggregate stats.

    Authorization:
    - Admins see all tenant rooms and stats
    - Regular users see only rooms they are members of
    - Legacy auth sees all rooms (backward compatibility)

    Response:
    {
        tenant_id, snapshot_at,
        totals: {messages_sent, messages_delivered, active_rooms, active_agents},
        rooms: [{room_id, room_name, message_count, active_agents(int), locked_files(int)}],
        top_senders: [{name, count}]
    }
    """
    backends = request.app.state.backends
    analytics_svc = request.app.state.analytics_service
    room_svc = request.app.state.room_service
    tid = _tid(auth)

    stats = await analytics_svc.get_stats(tid)

    presence_entries = await backends.presence.list_all(tid, _PRESENCE_TIMEOUT)
    total_active_agents: set[str] = {e["name"] for e in presence_entries}
    active_by_room: dict[str, list[str]] = {}
    for entry in presence_entries:
        room_ref = entry.get("room", "")
        if room_ref:
            active_by_room.setdefault(room_ref, []).append(entry["name"])

    # Get rooms based on authorization level
    is_admin = auth.role == "admin"
    if auth.is_legacy or is_admin:
        # Admins and legacy auth see all tenant rooms
        all_rooms = await room_svc.list_all(tid)
    else:
        # Regular users see only rooms they are members of
        all_rooms = await room_svc.list_by_member(tid, auth.sub or "")

    rooms_data: list[dict] = []
    for room in all_rooms:
        rid = room["id"]
        room_name = room.get("name", rid)
        history = await backends.room_history.get_recent(tid, rid, limit=200)
        msg_count = len(history)
        room_agents = list(set(
            active_by_room.get(rid, []) + active_by_room.get(room_name, [])
        ))
        await backends.room_state.expire_tasks(tid, rid)
        state = await backends.room_state.get(tid, rid)
        locked_count = len(state.get("locked_files", {}))
        rooms_data.append({
            "room_id": rid,
            "room_name": room_name,
            "message_count": msg_count,
            "active_agents": len(room_agents),
            "locked_files": locked_count,
        })

    rooms_data.sort(key=lambda r: r["message_count"], reverse=True)

    per_sender = stats.get("per_sender", {})
    top_senders = sorted(per_sender.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "tenant_id": tid,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "messages_sent": stats.get("total_sent", 0),
            "messages_delivered": stats.get("total_delivered", 0),
            "active_rooms": len(all_rooms),
            "active_agents": len(total_active_agents),
        },
        "rooms": rooms_data,
        "top_senders": [{"name": n, "count": c} for n, c in top_senders],
    }


@router.get("/usage/rooms/{room_id}")
async def get_room_usage(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Return usage breakdown for a specific room (includes state, agents, top senders).

    Requires room membership (403 if not a member).
    """
    backends = request.app.state.backends
    tid = _tid(auth)

    # Verify membership before returning usage data
    rid, room_data = await require_room_member(request, auth, tid, room_id)
    room_name = room_data.get("name", rid)

    await backends.room_state.expire_tasks(tid, rid)
    state = await backends.room_state.get(tid, rid)

    presence_entries = await backends.presence.list_all(tid, _PRESENCE_TIMEOUT)
    active_agents = sorted(
        e["name"] for e in presence_entries
        if e.get("room") in (rid, room_name)
    )
    if not active_agents:
        members = await backends.rooms.get_members(tid, rid)
        active_agents = sorted(members.keys())

    history = await backends.room_history.get_recent(tid, rid, limit=200)
    sender_counts: Counter[str] = Counter()
    bytes_sent = 0
    for msg in history:
        sender = msg.get("from_name", msg.get("from", "unknown"))
        sender_counts[sender] += 1
        bytes_sent += len((msg.get("content") or "").encode("utf-8"))

    return {
        "room_id": rid,
        "room_name": room_name,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "message_count": len(history),
        "bytes_sent": bytes_sent,
        "active_agents": active_agents,
        "locked_files": state.get("locked_files", {}),
        "active_goal": state.get("active_goal"),
        "top_senders": [{"name": n, "count": c} for n, c in sender_counts.most_common(10)],
    }
