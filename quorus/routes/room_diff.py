"""``GET /v1/rooms/{room}/commits/{sha}/diff``: one commit's diff for the board's inbox
(docs/plans/board-inbox.md, slice 5), shown beside the vote on a review or a block.

Read-only and narrow on purpose. Only the viewer's own JWT may ask (the shared secret
names nobody, so it gets 403 like ``/v1/social``); the caller must be a member of the
room; and bindings name this machine's own rooms in the shared-secret tenant, so another
tenant's room with the same name gets 404 (the scorecard's rule, codex on 1fae77f7). The
text is ``git show`` with helpers off and a byte cap (``runtime/commit_evidence``), so a
hostile repo can't run a diff driver or stall the relay; the relay never writes to git.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from quorus.auth.middleware import LEGACY_AUTH_REFUSED, AuthContext, verify_auth
from quorus.routes.room_auth import require_room_member
from quorus.routes.room_scorecard import _LEGACY_TENANT, bound_repo
from quorus.runtime import commit_evidence

router = APIRouter()
DIFF_BYTES = 65_536  # a review reads this much; the marker says when a commit was longer
CACHE_S = 60.0
# (tenant, resolved repo path, full sha) -> (when, text): a room re-bound to another repo
# or another tenant's room never gets a cached answer meant for someone else.
_cache: dict[tuple[str, str, str], tuple[float, str]] = {}


def _render(repo: Path, sha: str) -> str:
    """Resolve *sha* in *repo* and render it; "" when it is not a commit there."""
    full = commit_evidence.resolve(repo, sha)
    if full is None:
        return ""
    return commit_evidence.render(repo, full, limit=DIFF_BYTES) or ""


@router.get("/v1/rooms/{room_id}/commits/{sha}/diff", response_class=PlainTextResponse,
            include_in_schema=False)
async def room_commit_diff(room_id: str, sha: str, request: Request,
                           auth: AuthContext = Depends(verify_auth)) -> PlainTextResponse:
    if auth.is_legacy or not auth.sub:
        raise HTTPException(status_code=403, detail=LEGACY_AUTH_REFUSED)
    tid = auth.tenant_id or _LEGACY_TENANT
    _, room = await require_room_member(request, auth, tid, room_id)
    name = str(room.get("name") or room_id)
    repo = bound_repo(name) if tid == _LEGACY_TENANT else None
    if repo is None:
        raise HTTPException(status_code=404, detail="no repo bound to this room here")
    if not commit_evidence.HASH_RE.fullmatch(sha):
        raise HTTPException(status_code=404, detail="not a commit here")
    key = (tid, str(repo.resolve()), sha)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        text = hit[1]
    else:
        text = await asyncio.to_thread(_render, repo, sha)
        if text:
            _cache[key] = (time.time(), text)
    if not text:
        raise HTTPException(status_code=404, detail="not a commit here")
    return PlainTextResponse(text, headers={"cache-control": "private, max-age=60"})
