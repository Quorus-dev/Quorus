"""Agent identity and profile route handlers."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from quorus.auth.middleware import AuthContext, verify_auth

router = APIRouter()
_LEGACY_TENANT = "_legacy"
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))


def _tid(auth: AuthContext) -> str:
  return auth.tenant_id or _LEGACY_TENANT


@router.get("/agents/{name}")
async def get_agent_profile(
    name: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
  """Get public profile for an agent (rooms, last seen, message count)."""
  tid = _tid(auth)

  # Get agent presence info (last seen, online status)
  presence_svc = request.app.state.presence_service
  online_entries = await presence_svc.list_all(tid, HEARTBEAT_TIMEOUT)
  all_entries = await presence_svc.list_all(tid, timeout=86400 * 365 * 100)
  agent_entry = next((e for e in all_entries if e.get("name") == name), None)

  if not agent_entry:
    raise HTTPException(status_code=404, detail="Agent not found")

  online_names = {e.get("name", "") for e in online_entries}
  is_online = name in online_names

  # Get rooms for this agent
  room_svc = request.app.state.room_service
  all_rooms = await room_svc.list_all(tid)
  agent_rooms = [r for r in all_rooms if name in r.get("members", [])]

  # Get message count for this agent
  analytics_svc = request.app.state.analytics_service
  stats = await analytics_svc.get_stats(tid)
  message_count = stats.get("per_sender", {}).get(name, 0)

  return {
      "name": name,
      "rooms": [{"id": r["id"], "name": r["name"]} for r in agent_rooms],
      "last_seen": agent_entry.get("last_heartbeat", ""),
      "message_count": message_count,
      "online": is_online,
  }
