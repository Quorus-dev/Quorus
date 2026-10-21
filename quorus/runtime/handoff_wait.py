"""Durable, bounded busy-receiver waits outside the sender's wake handler."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime import handoff_runtime as runtime
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_state import Journal


def alert(daemon, room, cause):
    daemon._alert_owner(room, f"⚠ stuck: handoff {cause}; work kept unpublished. "
                        "Owner resolution required; no automatic takeover.")


def unchanged(worktree, capsule):
    git = Git(worktree.path)
    return (git.text("symbolic-ref", "HEAD") == f"refs/heads/{worktree.branch}"
            and git.text("rev-parse", "HEAD") == capsule.repo.head
            and git.text("rev-parse", "HEAD^{tree}") == capsule.repo.tree and git.clean())


async def notice(daemon, relay, envelope, target, bindings):
    """Tell the owner before the checkpoint, charging preparation to the cap."""
    room = envelope["room"]
    seconds = runtime.wait_seconds(bindings, room)
    deadline = time.time() + seconds
    expires = monotonic() + seconds
    line = (f"{daemon.config.participant_name} hit its limit; waiting for {target} "
            f"to finish its turn (up to {seconds / 60:g} min); work kept unpublished")
    await daemon._post_status(relay, room, envelope, line)
    return deadline, expires


async def start(daemon, relay, envelope, worktree, store, capsule, data, target,
                attempt_id, budget, event, select, deliver):
    room = envelope["room"]
    deadline, expires = budget
    with store.locked():
        store.write(capsule, "waiting", snapshot=capsule.model_dump(), data=data,
                    target=target, deadline=deadline, attempt_id=attempt_id)
    event("wait_started", target=target, deadline=deadline)

    def stop(cause):
        try:
            with store.locked():
                entry = store.read(capsule)
                if entry and entry["state"] == "waiting":
                    store.write(capsule, "failed", cause=cause)
            event("attempt_failed", cause=cause)
        finally:
            alert(daemon, room, cause)

    async def run():
        delay = 30
        async def stable():
            return await asyncio.wait_for(asyncio.to_thread(unchanged, worktree, capsule),
                                          max(0, expires - monotonic()))

        try:
            while monotonic() < expires:
                await asyncio.sleep(min(delay, max(0, expires - monotonic())))
                delay = min(delay * 2, 60)
                if monotonic() >= expires:
                    break
                # All relay IO is bounded by the remaining wall-clock budget.
                chosen, cause = await asyncio.wait_for(select(), expires - monotonic())
                if cause not in (None, "busy"):
                    stop(cause)
                    return
                if not await stable():
                    stop("stale")
                    return
                if chosen:
                    # No sleeping under the wake lock. Other wakes run read-only
                    # under the waiting journal fence; publication is serialized.
                    if daemon._wake_lock.locked():
                        continue
                    async with daemon._wake_lock:
                        chosen, cause = await asyncio.wait_for(select(), expires - monotonic())
                        if not chosen:
                            if cause != "busy":
                                stop(cause)
                                return
                            continue
                        if not await stable():
                            stop("stale")
                            return
                        await asyncio.wait_for(deliver(chosen), expires - monotonic())
                    return
            stop("busy_timeout")
        except asyncio.CancelledError:
            stop("interrupted")
            raise
        except TimeoutError:
            stop("busy_timeout")
        except Exception:
            try:
                stop("wait_failed")
            except Exception:
                pass  # the durable waiting fence and owner alert survive IO failure

    task = asyncio.create_task(run())
    daemon._bg_tasks.add(task)
    task.add_done_callback(daemon._bg_tasks.discard)


def interrupt_pending(daemon):
    """On boot, fail closed on waits owned by the previous process; never resend.

    This runs before the publication sweep and does not read any vendor config.
    Partial/corrupt journals are left intact and surfaced to the owner.
    """
    for path in daemon.config.runtime_dir.glob("handoffs-*.json"):
        try:
            rows = json.loads(path.read_text())
            for entry in rows.values():
                if entry.get("state") != "waiting":
                    continue
                room, _, repo = entry["scope"].partition(":")
                capsule = HandoffCapsule.model_validate(entry["snapshot"])
                store = Journal(path, room, Path(repo), hold_dir=daemon._unfinished_dir)
                with store.locked():
                    store.write(capsule, "failed", cause="interrupted")
                store.event(dict(event="attempt_failed", cause="interrupted",
                    attempt_id=entry["attempt_id"], room=room,
                    agent=daemon.config.participant_name,
                    at=datetime.now(timezone.utc).isoformat()))
                alert(daemon, room, "interrupted")
        except Exception:
            alert(daemon, "", "recovery failed")
