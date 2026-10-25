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
# Bounded, or members could park arbitrarily many 64 KB diffs in relay memory (codex on
# f3ba7b4a): at most this many rendered diffs, oldest out first, expired entries dropped
# on every touch. Keys use the commit's full sha, so `abc1234` and its 40-character form
# share one entry; the small alias map remembers what a short form resolved to.
MAX_ENTRIES = 32


class _Cache:
    """Insertion-ordered, time- and size-bounded; one per thing cached."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.items: dict[tuple, tuple[float, object]] = {}

    def get(self, key: tuple, now: float):
        self._sweep(now)
        hit = self.items.get(key)
        return hit[1] if hit else None

    def put(self, key: tuple, value: object, now: float) -> None:
        self._sweep(now)
        self.items.pop(key, None)
        self.items[key] = (now, value)
        while len(self.items) > self.cap:
            self.items.pop(next(iter(self.items)))  # the oldest insertion

    def _sweep(self, now: float) -> None:
        for key in [k for k, (at, _) in self.items.items() if now - at >= CACHE_S]:
            self.items.pop(key, None)


_now = time.time  # patched by the expiry test
_diffs = _Cache(MAX_ENTRIES)    # (tenant, resolved repo path, full sha) -> text
_aliases = _Cache(MAX_ENTRIES)  # (tenant, resolved repo path, sha as given) -> full sha


def _resolve(repo: Path, sha: str) -> str | None:
    return commit_evidence.resolve(repo, sha)


def _render(repo: Path, full: str) -> str:
    """`git show` of the resolved commit, cut at the cap; "" when git has nothing."""
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
    where = str(repo.resolve())
    now = _now()
    full = _aliases.get((tid, where, sha), now)
    if full is None:
        full = await asyncio.to_thread(_resolve, repo, sha)
        if full is None:
            raise HTTPException(status_code=404, detail="not a commit here")
        _aliases.put((tid, where, sha), full, now)
    text = _diffs.get((tid, where, full), now)
    if text is None:
        text = await asyncio.to_thread(_render, repo, full)
        if not text:
            raise HTTPException(status_code=404, detail="not a commit here")
        _diffs.put((tid, where, full), text, now)
    return PlainTextResponse(text, headers={"cache-control": "private, max-age=60"})
