"""Daemon completion hook; only locally registered verified turns can use it."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime.handoff_ownership import owns_claim
from quorus.runtime.handoff_receiver import complete
from quorus.runtime.handoff_state import Journal, StateError


@dataclass(frozen=True)
class Continuation:
    capsule: HandoffCapsule
    journal: Journal
    root: Path
    room: str
    task_id: str


async def finish(turn: Continuation, relay, *, participant: str, room: str,
                 root: Path) -> None:
    """A different wake/room/worktree cannot release this continuation's hold."""
    if room != turn.room or root.resolve() != turn.root.resolve():
        raise OSError("handoff completion context differs; kept unpublished")
    claimed = await owns_claim(relay, room=room, participant=participant, task_id=turn.task_id)
    try:
        allowed = await asyncio.to_thread(complete, root, turn.journal, turn.capsule,
                                          claimed=claimed)
    except (OSError, StateError) as exc:
        raise OSError("handoff completion could not be verified; kept unpublished") from exc
    if not allowed:
        raise OSError("handoff completion not authorized; kept unpublished")
