"""Carry review requests' commits between machines (docs/design/cross-machine-code.md).

Opt-in per room (``"share": true`` in room-bindings.json).

* Sharer (step 3): when an agent's final reply @-mentions a teammate and the
  wake produced commits, the daemon bundles exactly those commits (head before
  the wake .. head now) and appends a fetch line.
* Reviewer (step 4): when a wake message carries such a fetch line, the daemon
  fetches the bundle into ``refs/quorus/<uploader>/<branch>`` before the agent
  runs and tells it what to ``git show``. Nothing is checked out or merged.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from quorus.bundle_client import BundleClientError, download, fetch_into, make_bundle, upload

logger = logging.getLogger("reflexd.review_share")
MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9][A-Za-z0-9._-]{0,63})")
# The line attach_bundle writes; only its exact form is fetched automatically.
FETCH_LINE = re.compile(r"`quorus fetch (b_[A-Za-z0-9_-]{1,40}) --room [^`\s]+`")
MAX_AUTO_FETCHES = 3  # per wake: a message can't make the daemon download a pile


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


def shared_bundle_ids(message: str) -> list[str]:
    """Bundle ids in *message*'s fetch lines, first few, without repeats."""
    ids: list[str] = []
    for bundle_id in FETCH_LINE.findall(message or ""):
        if bundle_id not in ids:
            ids.append(bundle_id)
    return ids[:MAX_AUTO_FETCHES]


async def fetch_shared(*, relay_url: str, bearer: str, room: str, worktree: Any,
                       message: str) -> str:
    """Fetch the bundles *message* shares; a prompt note saying what landed, or ""."""
    ids = shared_bundle_ids(message)
    if not ids:
        return ""
    notes = []
    for bundle_id in ids:
        try:
            data, meta = await asyncio.to_thread(download, relay_url, bearer, room, bundle_id)
            got = await asyncio.to_thread(fetch_into, Path(worktree.path), data, meta)
        except BundleClientError as exc:  # the agent can still fetch by hand
            logger.warning("auto-fetch of %s failed: %s", bundle_id, exc)
            notes.append(f"- {bundle_id}: not fetched ({str(exc)[:200]}). Try "
                         f"`quorus fetch {bundle_id} --room {room}` yourself.")
            continue
        logger.info("auto-fetched %s into %s", bundle_id, got.ref)
        notes.append(f"- @{got.uploader}'s {got.branch} @{got.head[:7]} is in `{got.ref}`: "
                     f"`git show {got.head[:7]}`, or `git log --stat -5 {got.ref}`.")
    return ("# Shared code\nThe triggering message shares commits for review. Quorus "
            "fetched them into this repo as read-only refs (nothing checked out or "
            "merged). Review the code itself, not just the message:\n"
            + "\n".join(notes) + "\n\n")
