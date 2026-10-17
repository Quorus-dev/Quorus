"""Attach this wake's commits to a review request (docs/weekend/cross-machine-code.md, step 3).

Opt-in per room (``"share": true`` in room-bindings.json). When an agent's
final reply @-mentions a teammate and the wake produced commits, the daemon
bundles exactly those commits (head before the wake .. head now) and appends a
fetch line, so a reviewer on another machine can ``git show`` them instead of
reading a pasted diff.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from typing import Any

from quorus.bundle_client import BundleClientError, make_bundle, upload

logger = logging.getLogger("reflexd.review_share")
MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9][A-Za-z0-9._-]{0,63})")


def mentions_teammate(reply: str, me: str) -> bool:
    return any(name != me for name in MENTION.findall(reply or ""))


def head_of(worktree: Any) -> str | None:
    """The agent branch's commit, or None if it can't be read."""
    proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                           f"refs/heads/{worktree.branch}"],
                          cwd=worktree.path, capture_output=True, text=True)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


async def attach_bundle(*, relay_url: str, bearer: str, room: str, worktree: Any,
                        pre_head: str | None, reply: str, me: str) -> str | None:
    """The fetch line to append to *reply*, or None when there is nothing to share."""
    if not pre_head or not mentions_teammate(reply, me):
        return None
    head = await asyncio.to_thread(head_of, worktree)
    if not head or head == pre_head:
        return None
    try:
        data, branch = await asyncio.to_thread(
            make_bundle, worktree.path, branch=worktree.branch, base=pre_head)
        meta = await asyncio.to_thread(upload, relay_url, bearer, room, data)
    except BundleClientError as exc:  # sharing is a convenience: never lose the reply
        logger.warning("review bundle not shared: %s", exc)
        return None
    return (f"📦 {branch} @{head[:7]} is shared for review: "
            f"`quorus fetch {meta['id']} --room {room}` (or the fetch_bundle tool)")
