"""Daemon completion hook; only locally registered verified turns can use it."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_hold import PublicationHold
from quorus.runtime.handoff_ownership import owns_claim
from quorus.runtime.handoff_receiver import complete
from quorus.runtime.handoff_state import Journal, StateError, fingerprint


@dataclass(frozen=True)
class Continuation:
    capsule: HandoffCapsule
    journal: Journal
    root: Path
    room: str
    task_id: str


async def finish(turn: Continuation, relay, *, participant: str, room: str,
                 root: Path, commit=None) -> None:
    """A different wake/room/worktree cannot release this continuation's hold."""
    if room != turn.room or root.resolve() != turn.root.resolve():
        raise OSError("handoff completion context differs; kept unpublished")
    claimed = await owns_claim(relay, room=room, participant=participant, task_id=turn.task_id)
    if commit is not None:
        if not claimed:
            raise OSError("handoff claim changed; kept unpublished")
        await asyncio.to_thread(prepare, turn, root, commit)
        # Committing can take time: completion still needs a fresh claim.
        claimed = await owns_claim(relay, room=room, participant=participant, task_id=turn.task_id)
    try:
        allowed = await asyncio.to_thread(complete, root, turn.journal, turn.capsule,
                                          claimed=claimed)
    except (OSError, StateError) as exc:
        raise OSError("handoff completion could not be verified; kept unpublished") from exc
    if not allowed:
        raise OSError("handoff completion not authorized; kept unpublished")


def prepare(turn: Continuation, root: Path, commit) -> None:
    """Commit only this registered continuation, never a foreign/finished turn."""
    with turn.journal.locked():
        entry = turn.journal.read(turn.capsule)
        git = Git(root)
        if (entry is None or entry["state"] not in {"continuing", "done"}
                or not PublicationHold(root, turn.journal, turn.capsule).owned()
                or git.text("symbolic-ref", "HEAD") != f"refs/heads/{entry.get('branch')}"):
            raise OSError("handoff commit not authorized; kept unpublished")
        head = git.text("rev-parse", "HEAD")
        git.run("merge-base", "--is-ancestor", turn.capsule.repo.head, head)
        if entry["state"] == "done":
            if head != entry.get("completed_head") or not git.clean():
                raise OSError("completed handoff changed; kept unpublished")
            return
        if not git.clean():
            commit()
        if not Git(root).clean():
            raise OSError("handoff commit failed; kept unpublished")


def record(turn: Continuation, worktree, participant: str, *, published: bool,
           cause: str) -> None:
    """Publication evidence is separate from proof and never grants authority."""
    head = None
    if published:
        try:
            git = Git(worktree.path)
            head = git.text("rev-parse", "HEAD")
            entry = turn.journal.read(turn.capsule)
            if not entry or entry["state"] != "done" or not git.clean():
                raise StateError("completion not done")
            git.run("merge-base", "--is-ancestor", head, worktree.main)
        except (OSError, StateError):
            published, cause = False, "publish_unverified"
    result = {"result": "published" if published else "kept_unpublished",
              "cause": "" if published else cause, "head": head,
              "at": datetime.now(timezone.utc).isoformat()}
    with turn.journal.locked():
        turn.journal.record_completion(turn.capsule, result)
        turn.journal.event(dict(event="completion", fingerprint=fingerprint(turn.capsule),
                                room=turn.room, agent=participant, **result))
