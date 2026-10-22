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

import fcntl
import hashlib
import json
import os
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


def daemon_lock_path(runtime_dir: Path, agent: str) -> Path:
    """Held by *agent*'s daemon for its whole life, and by a release while it runs:
    while one holds it, the other can't start (Codex on ac0bc90b)."""
    return runtime_dir / f"daemon-{hashlib.sha256(agent.encode()).hexdigest()[:24]}.lock"


def hold_daemon_lock(path: Path) -> int | None:
    """An open fd holding the lock, or None if someone else holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def release(runtime_dir: Path, *, agent: str, room: str, capsule_id: str, head: str,
            action: str, daemon_running: bool | None, resolve_claim=None) -> str:
    """Release one hold after the owner looked at *head*. Raises Refused.

    *daemon_running* is the caller's process check: None (couldn't tell) refuses,
    like True. The daemon lock below is the authority for daemons that hold it."""
    if action not in ("publish", "discard"):
        raise Refused("choose --publish or --discard")
    if daemon_running is None:
        raise Refused(f"couldn't tell whether @{agent}'s daemon is running; nothing changed")
    if daemon_running:
        raise Refused(f"stop @{agent}'s daemon first; a running daemon may be writing")
    if not FULL_SHA.fullmatch(head):
        raise Refused("--head must be the full 40-character commit you reviewed")
    lock = hold_daemon_lock(daemon_lock_path(runtime_dir, agent))
    if lock is None:
        raise Refused(f"@{agent}'s daemon (or another release) holds its lock; stop it first")
    try:
        return _release(runtime_dir, agent, room, capsule_id, head, action, resolve_claim)
    finally:
        os.close(lock)  # held for the whole release: no daemon starts mid-way


def _release(runtime_dir: Path, agent: str, room: str, capsule_id: str, head: str,
             action: str, resolve_claim=None) -> str:
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
    record = {"agent": agent, "room": room, "capsule": capsule_id, "head": head,
              "action": action, "backup": backup_ref(capsule_id)}
    try:
        with journal.locked():  # a daemon mid-gate holds this lock
            entry = journal.read(capsule)
            if not entry or entry.get("fingerprint") != held.entry.get("fingerprint"):
                raise Refused("the journal entry changed; list holds again")
            hold = PublicationHold(held.worktree, journal, capsule)
            if not hold.owned():
                raise Refused("the hold marker no longer matches this capsule")
            resuming = _decided(runtime_dir, record)
            # The decision is on disk before anything moves, so a crash at any later
            # step leaves a record the owner's rerun resumes from.
            if not resuming:
                _log(runtime_dir, {**record, "at": time.time(), "step": "decided"})
            if action == "discard" and entry.get("task_id") and resolve_claim is None:
                raise Refused("discard needs exact relay claim resolution; use the owner CLI")
            note = _move(Git(held.worktree), held, entry, head, action, resuming)
            if action == "discard":
                if entry.get("task_id"):
                    resolve_claim(room=room, agent=agent, task_id=entry["task_id"],
                                  resolution_id=entry["fingerprint"])
                journal.discard(capsule)
            hold.release()
    except StateError as exc:
        raise Refused(str(exc)) from exc
    _log(runtime_dir, {**record, "at": time.time(), "step": "done"})
    return note


def _decided(runtime_dir: Path, record: dict) -> bool:
    """A decision for exactly this release was recorded and never finished."""
    path = runtime_dir / RELEASE_LOG
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return False
    steps = [json.loads(x).get("step") for x in lines if x.strip()
             and {k: json.loads(x).get(k) for k in record} == record]
    return bool(steps) and steps[-1] == "decided"


def _move(git: Git, held: HeldWork, entry: dict, head: str, action: str,
          resuming: bool) -> str:
    branch_ref = f"refs/heads/{held.branch}"
    ref = backup_ref(held.capsule_id)
    prev = entry.get("prev")
    if git.text("symbolic-ref", "-q", "HEAD") != branch_ref:
        raise Refused(f"the worktree isn't on {held.branch}")
    existing = git.run("for-each-ref", "--format=%(objectname)", ref).decode().strip()
    current = git.text("rev-parse", "HEAD")
    discarded = f"discarded: {held.branch} back at {str(prev)[:12]}; {head[:12]} kept at {ref}"
    if resuming and action == "discard" and existing == head and _valid(prev):
        if current == head and _tree_is(git, prev):
            # Crashed after the tree went back but before the branch moved: finish
            # the compare-and-swap (Codex on a9a1ca67). Any other edit isn't this.
            git.run("update-ref", branch_ref, prev, head)
            return _settled(git, prev, discarded)
        if current == prev:
            return _settled(git, prev, discarded)  # crashed after the branch moved
    if not git.clean():
        raise Refused("the worktree has uncommitted changes")
    if current != head:
        raise Refused("the branch moved since you reviewed it; look again")
    if existing and existing != head:
        raise Refused(f"{ref} already points elsewhere; resolve by hand")
    if not existing:
        git.run("update-ref", ref, head, "")  # create only
    if action == "publish":
        # Look again right before the hold goes: the head lifted is the head reviewed
        # (Codex on ac0bc90b: a commit landing after the backup was released).
        if (git.text("rev-parse", branch_ref) != head or git.text("rev-parse", "HEAD") != head
                or not git.clean()):
            raise Refused("the branch changed during the release; nothing was lifted")
        return (f"hold lifted at {head[:12]} (backup {ref}); @{held.agent}'s publish "
                "sweep publishes it when its daemon starts")
    if not _valid(prev):
        raise Refused("the journal has no previous head to go back to; resolve by hand")
    # Tree first, with a two-way merge that refuses to overwrite a local edit (no
    # reset --hard: Codex on a9a1ca67), then the branch by compare-and-swap.
    try:
        git.run("read-tree", "-u", "-m", head, prev)
    except StateError as exc:
        raise Refused("an edit made during the release is in the way; it's kept, and "
                      "so is the hold") from exc
    git.run("update-ref", branch_ref, prev, head)
    return _settled(git, prev, discarded)


def _valid(sha) -> bool:
    return isinstance(sha, str) and bool(FULL_SHA.fullmatch(sha))


def _tree_is(git: Git, commit: str) -> bool:
    """Index and worktree are exactly *commit*'s tree, with nothing untracked."""
    return (git.text("write-tree") == git.text("rev-parse", f"{commit}^{{tree}}")
            and not git.run("diff", "--name-only")
            and not git.run("ls-files", "--others", "--exclude-standard"))


def _settled(git: Git, prev: str, note: str) -> str:
    """The branch is at prev; only a clean tree lets the hold go. An edit that
    landed during the release survives, and the hold stays for the owner."""
    if git.text("rev-parse", "HEAD") != prev:
        raise Refused("the branch didn't land on its previous head; check it by hand")
    if not git.clean():
        raise Refused("the branch is back at its previous head, but an edit made during "
                      "the release is kept; the hold stays until you resolve it")
    return note


def _log(runtime_dir: Path, record: dict) -> None:
    """Append and fsync: the decision must reach disk before the hold goes."""
    path = runtime_dir / RELEASE_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
