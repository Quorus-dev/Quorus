"""``GET /v1/rooms/{room}/scorecard``: the outcome ledger per agent, for the board.

The board shows, next to each agent, how its work in this room's repo held up
(commits, tests, cross-vendor review verdicts, reverts, lines still in the code).
Only the relay that has the room's repo binding on its machine can answer (the
default local relay); any other gets 404 and the board simply hides the line.
Members only, aggregates only (no commit text or paths), cached for 60 s.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from quorus import ledger
from quorus.auth.middleware import AuthContext, verify_auth
from quorus.routes.room_auth import require_room_member

router = APIRouter()
_LEGACY_TENANT = "_legacy"
CACHE_S = 60.0
_SINCE = re.compile(r"^\d{1,3} (hours|days|weeks) ago$")
_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def bound_repo(room: str) -> Path | None:
    """The repo this machine's agents work in for *room*, if any."""
    try:
        data = json.loads((Path.home() / ".quorus" / "room-bindings.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = data.get(room) if isinstance(data, dict) else None
    path = entry.get("path") if isinstance(entry, dict) else entry
    if not isinstance(path, str) or not path:
        return None
    repo = Path(path).expanduser()
    return repo if (repo / ".git").exists() else None


@router.get("/v1/rooms/{room_id}/scorecard")
async def room_scorecard(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
    since: str = "14 days ago",
) -> dict[str, Any]:
    if not _SINCE.match(since):
        raise HTTPException(status_code=422, detail="since must look like '14 days ago'")
    tid = auth.tenant_id or _LEGACY_TENANT
    _, room = await require_room_member(request, auth, tid, room_id)
    name = str(room.get("name") or room_id)
    repo = bound_repo(name)
    if repo is None:
        raise HTTPException(status_code=404, detail="no repo bound to this room here")
    key = (tid, name, since)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    history = await request.app.state.room_msg_service.history(tid, room_id, 500)
    rows = await asyncio.to_thread(ledger.outcomes, repo, f"--since={since}", history)
    agents = [
        {k: r.get(k) for k in ("agent", "commits", "with_tests", "approved", "flagged",
                               "reverted", "survival", "duels_won", "duels_lost")}
        for r in ledger.scorecard(rows, key="agent")
    ]
    out = {"room": name, "since": since, "commits": len(rows), "agents": agents}
    _cache[key] = (time.time(), out)
    return out
