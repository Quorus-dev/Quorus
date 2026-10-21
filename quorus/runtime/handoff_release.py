"""List and release handoff publication holds, for the owner only (slice 2c-2).

Dogfood plan §1 and §5 (docs/internal/bets/never-stuck-dogfood.md, Codex GO on
cfd69e70). Today a handoff hold can only be lifted by a writable recovery, which
needs the room's switch on. This is the owner's way out when it's off or stuck.

A release refuses unless every one of these holds:
- the agent's daemon is stopped, and the journal lock is free (no gate running);
- exactly one hold matches the room, agent journal and capsule id, and its marker's
  fingerprint is the journal entry's;
- the worktree is on the entry's branch, at exactly the head the owner approved,
  with a clean tree;
- a backup ref for that head exists before anything moves.

``discard`` then moves the branch back to the journal's ``prev`` by compare-and-swap
from the approved head. ``publish`` only lifts the hold: the daemon's own publish
sweep publishes on its next start, through the normal path. Neither ever pushes.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_hold import PublicationHold
from quorus.runtime.handoff_state import Journal, StateError

BACKUP_PREFIX = "refs/quorus/handoff-backup/"
RELEASE_LOG = "handoff-releases.jsonl"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class Refused(Exception):
    """A release that didn't happen, with a reason the owner can act on."""


@dataclass(frozen=True)
class HeldWork:
    marker: Path
    worktree: Path
    room: str
    capsule_id: str
    state: str
    branch: str
    journal: Path | None = None
    entry: dict = field(default_factory=dict)

    @property
    def agent(self) -> str:
        return self.branch.removeprefix("quorus/")


def journal_path(runtime_dir: Path, agent: str) -> Path:
    """Where reflexd keeps *agent*'s handoff journal (handoff_runtime.journal)."""
    return runtime_dir / f"handoffs-{hashlib.sha256(agent.encode()).hexdigest()[:24]}.json"


def backup_ref(capsule_id: str) -> str:
    """Capsule ids contain ':', which git refs can't (Codex on rev 3): hash it."""
    return BACKUP_PREFIX + hashlib.sha256(capsule_id.encode()).hexdigest()[:24]


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def holds(runtime_dir: Path) -> list[HeldWork]:
    """Every handoff hold, matched to its journal entry. Read-only; a hold with no
    matching entry is listed too, as ``unknown``, so the owner sees it."""
    entries: dict[str, tuple[Path, dict]] = {}
    for path in sorted(runtime_dir.glob("handoffs-*.json")):
        data = _read(path)
        for entry in (data.values() if isinstance(data, dict) else ()):
            if isinstance(entry, dict) and isinstance(entry.get("fingerprint"), str):
                entries[entry["fingerprint"]] = (path, entry)
    out = []
    for marker in sorted((runtime_dir / "unfinished-wakes").glob("*.json")):
        value = _read(marker)
        if not isinstance(value, dict) or not isinstance(value.get("handoff"), str):
            continue
        branch = str(value.get("branch", "")).removesuffix("-unfinished")
        journal, entry = entries.get(value["handoff"], (None, {}))
        room = str(entry.get("scope", "")).partition(":")[0]
        out.append(HeldWork(marker, Path(str(value.get("path", ""))), room,
                            str(entry.get("id", "")), str(entry.get("state", "unknown")),
                            str(entry.get("branch") or branch), journal, entry))
    return out


def release(runtime_dir: Path, *, agent: str, room: str, capsule_id: str, head: str,
            action: str, daemon_running: bool) -> str:
    """Release one hold after the owner looked at *head*. Raises Refused."""
    if action not in ("publish", "discard"):
        raise Refused("choose --publish or --discard")
    if daemon_running:
        raise Refused(f"stop @{agent}'s daemon first; a running daemon may be writing")
    if not FULL_SHA.fullmatch(head):
        raise Refused("--head must be the full 40-character commit you reviewed")
    mine = journal_path(runtime_dir, agent)
    found = [h for h in holds(runtime_dir)
             if h.journal == mine and h.room == room and h.capsule_id == capsule_id]
    if len(found) != 1:
        raise Refused("no single hold matches that room, agent and capsule "
                      "(see quorus handoff holds)")
    held = found[0]
    try:
        capsule = HandoffCapsule.model_validate(held.entry["snapshot"])
    except (KeyError, ValueError) as exc:
        raise Refused("the journal entry has no readable capsule; resolve by hand") from exc
    repo = Path(str(held.entry.get("scope", "")).partition(":")[2])
    journal = Journal(mine, room, repo, hold_dir=runtime_dir / "unfinished-wakes")
    try:
        with journal.locked():  # a daemon mid-gate holds this lock
            entry = journal.read(capsule)
            if not entry or entry.get("fingerprint") != held.entry.get("fingerprint"):
                raise Refused("the journal entry changed; list holds again")
            hold = PublicationHold(held.worktree, journal, capsule)
            if not hold.owned():
                raise Refused("the hold marker no longer matches this capsule")
            note = _move(Git(held.worktree), held, entry, head, action)
            hold.release()
    except StateError as exc:
        raise Refused(str(exc)) from exc
    _log(runtime_dir, {"at": time.time(), "agent": agent, "room": room,
                       "capsule": capsule_id, "head": head, "action": action,
                       "backup": backup_ref(capsule_id)})
    return note


def _move(git: Git, held: HeldWork, entry: dict, head: str, action: str) -> str:
    branch_ref = f"refs/heads/{held.branch}"
    if git.text("symbolic-ref", "-q", "HEAD") != branch_ref:
        raise Refused(f"the worktree isn't on {held.branch}")
    if git.text("rev-parse", "HEAD") != head:
        raise Refused("the branch moved since you reviewed it; look again")
    if not git.clean():
        raise Refused("the worktree has uncommitted changes")
    ref = backup_ref(held.capsule_id)
    existing = git.run("for-each-ref", "--format=%(objectname)", ref).decode().strip()
    if existing and existing != head:
        raise Refused(f"{ref} already points elsewhere; resolve by hand")
    if not existing:
        git.run("update-ref", ref, head, "")  # create only
    if action == "publish":
        return (f"hold lifted at {head[:12]} (backup {ref}); @{held.agent}'s publish "
                "sweep publishes it when its daemon starts")
    prev = entry.get("prev")
    if not isinstance(prev, str) or not FULL_SHA.fullmatch(prev):
        raise Refused("the journal has no previous head to go back to; resolve by hand")
    git.run("update-ref", branch_ref, prev, head)  # compare-and-swap from the approved head
    git.run("reset", "--hard", "-q", "HEAD")
    if git.text("rev-parse", "HEAD") != prev:
        raise Refused("the branch didn't land on its previous head; check it by hand")
    return f"discarded: {held.branch} back at {prev[:12]}; {head[:12]} kept at {ref}"


def _log(runtime_dir: Path, record: dict) -> None:
    path = runtime_dir / RELEASE_LOG
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    path.chmod(0o600)
