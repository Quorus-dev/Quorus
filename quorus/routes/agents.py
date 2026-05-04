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
  """Get public profile for an agent (rooms, last seen, message count).

  Wave-5 Fix 5 — enumeration scoping. Previously this endpoint returned
  the full room list and message count for ANY agent in the tenant
  given any valid auth, an OSINT vector that let attackers map the
  internal agent topology. We now restrict the response to:

  * admins / legacy auth (full visibility), OR
  * the caller themselves, OR
  * agents that share at least one room with the caller — and even then
    only the rooms both parties co-occupy are returned.

  Non-members of any shared room get a 403; this is intentionally louder
  than 404 so we don't leak existence vs. non-membership.
  """
  tid = _tid(auth)

  # Get agent presence info (last seen, online status). Single list_all
  # call — the backend tags each entry with ``_online`` against the
  # supplied timeout. The previous two-call pattern created a second
  # cache bucket that defeated the presence cache.
  presence_svc = request.app.state.presence_service
  entries = await presence_svc.list_all(tid, HEARTBEAT_TIMEOUT)
  agent_entry = next((e for e in entries if e.get("name") == name), None)

  if not agent_entry:
    raise HTTPException(status_code=404, detail="Agent not found")

  is_online = bool(agent_entry.get("_online"))

  # Get rooms for this agent
  room_svc = request.app.state.room_service
  all_rooms = await room_svc.list_all(tid)
  agent_rooms_raw = [r for r in all_rooms if name in r.get("members", [])]

  # Wave-5 Fix 5 — visibility gate. Admins and legacy auth keep full
  # visibility (operational tooling depends on it). Everyone else can
  # only see the rooms they share with the queried agent.
  is_admin = auth.is_legacy or auth.role == "admin"
  caller = auth.sub
  if is_admin or (caller is not None and caller == name):
    visible_rooms = agent_rooms_raw
  else:
    visible_rooms = [
        r for r in agent_rooms_raw
        if caller is not None and caller in r.get("members", [])
    ]
    if not visible_rooms:
      # No shared rooms = caller has no business knowing this agent's
      # profile. 403 instead of 404 because the agent does exist; we're
      # explicitly refusing rather than feigning ignorance.
      raise HTTPException(
          status_code=403,
          detail="Not authorized to view this agent profile",
      )

  # Get message count for this agent (admin-only — non-admins should
  # not see traffic volume of agents they only share one room with).
  if is_admin or (caller is not None and caller == name):
    analytics_svc = request.app.state.analytics_service
    stats = await analytics_svc.get_stats(tid)
    message_count = stats.get("per_sender", {}).get(name, 0)
  else:
    message_count = 0

  return {
      "name": name,
      "rooms": [{"id": r["id"], "name": r["name"]} for r in visible_rooms],
      "last_seen": agent_entry.get("last_heartbeat", ""),
      "message_count": message_count,
      "online": is_online,
  }
