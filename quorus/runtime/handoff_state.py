"""Private, durable receiver journal. A duplicate never starts another turn."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from quorus.private_files import write_private_json
from quorus.protocol.handoff_capsule import HandoffCapsule

STATES = frozenset({"received", "adopting", "adopted", "verified", "continuing", "done",
                    "declined", "stale", "unverifiable", "already_passing", "sent", "failed"})
TERMINAL = frozenset({"done", "declined", "stale", "unverifiable", "already_passing"})


class StateError(ValueError):
    """Unreadable or inconsistent state: ask the owner, never assume permission."""


class Duplicate(StateError):
    """The delivery is already recorded, including an interrupted writable turn."""


def fingerprint(capsule: HandoffCapsule) -> str:
    return hashlib.sha256(capsule.model_dump_json().encode()).hexdigest()


class Journal:
    """One journal per receiver, with a cross-process lock around each gate.

    Entries are scoped to room AND repository. IDs alone aren't globally unique.
    Active entries are never evicted to make room; a full journal fails closed.
    The caller supplies the daemon-owned private path, never a capsule path.
    """

    def __init__(self, path: Path, room: str, repo: Path, *, hold_dir: Path | None = None):
        self.path = path
        self.repo = repo.resolve()
        self.scope = f"{room}:{self.repo}"
        self.hold_dir = hold_dir

    def key(self, capsule: HandoffCapsule) -> str:
        return hashlib.sha256(f"{self.scope}:{capsule.id}".encode()).hexdigest()

    @contextmanager
    def locked(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The containing directory belongs to the daemon. No lock unlink on exit:
        # waiters must all keep locking the same inode.
        fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StateError("handoff journal lock unavailable; keep work fenced") from exc
            yield
        finally:
            os.close(fd)

    def _load(self) -> dict:
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return {}
        if len(raw.encode()) > 8 * 1024 * 1024:
            raise StateError("handoff journal too large")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise StateError("invalid handoff journal") from exc
        if not isinstance(data, dict) or any(
                not isinstance(v, dict) or not isinstance(v.get("state"), str)
                or v["state"] not in STATES
                for v in data.values()):
            raise StateError("invalid handoff state")
        return data

    def read(self, capsule: HandoffCapsule) -> dict | None:
        entry = self._load().get(self.key(capsule))
        if entry is not None and entry.get("fingerprint") != fingerprint(capsule):
            raise StateError("capsule id reused with different data")
        return dict(entry) if entry is not None else None

    def pending_sends(self, *, all_rooms: bool = False) -> list[dict]:
        return [dict(e) for e in self._load().values()
                if e["state"] == "sent" and (e.get("scope") == self.scope
                    or (all_rooms and str(e.get("scope", "")).endswith(f":{self.repo}")))]

    def _save(self, data: dict) -> None:
        write_private_json(self.path, data)
        # The record must reach disk before the action it guards.
        with self.path.open("rb") as stream:
            os.fsync(stream.fileno())
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def write(self, capsule: HandoffCapsule, state: str, **fields) -> dict:
        if state not in STATES:
            raise StateError("unknown handoff state")
        data = self._load()
        old = self.read(capsule)
        if old and old["state"] in TERMINAL | {"continuing"}:
            raise Duplicate("handoff already handled")
        if not old and len(data) >= 1024:
            raise StateError("handoff journal full; owner needs to archive it")
        entry = {**(old or {}), **fields, "fingerprint": fingerprint(capsule),
                 "state": state, "id": capsule.id, "head": capsule.repo.head,
                 "tree": capsule.repo.tree, "root": capsule.root, "scope": self.scope}
        data[self.key(capsule)] = entry
        self._save(data)
        return entry

    def authorize_retry(self, capsule: HandoffCapsule, wake_id: str, head: str) -> None:
        """A continuing record may authorize each explicit owner retry only once."""
        data = self._load()
        entry = self.read(capsule)
        if entry is None or entry["state"] != "continuing":
            raise StateError("no interrupted continuation")
        retries = entry.get("retry_wakes", [])
        if not isinstance(retries, list) or len(retries) >= 16:
            raise StateError("invalid/full retry ledger")
        if wake_id in retries:
            raise Duplicate("owner retry already launched")
        data[self.key(capsule)] = {**entry, "retry_wakes": [*retries, wake_id],
                                   "retry_head": head}
        self._save(data)

    @property
    def events_path(self) -> Path:
        return self.path.with_name(self.path.stem + "-events.jsonl")

    def event(self, record: dict) -> None:
        """Append one event and fsync it before returning (dogfood plan §1). Events
        are what the read-only report counts, including attempts that never got a
        capsule. No lock: one O_APPEND write is atomic, and callers may already hold
        the journal lock. A full file fails closed, like a full journal."""
        path = self.events_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise StateError("handoff event log full; owner needs to archive it")
        except FileNotFoundError:
            pass
        line = (json.dumps({**record, "scope": self.scope}, sort_keys=True) + "\n").encode()
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                     0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def record_receipt(self, capsule: HandoffCapsule, text: str, finished: str) -> None:
        """Attach observed evidence without reopening a terminal authority state."""
        data = self._load()
        entry = self.read(capsule)
        if entry is None:
            raise StateError("receipt without registered delivery")
        data[self.key(capsule)] = {**entry, "receipt": text, "check_finished": finished}
        self._save(data)

    def finish(self, capsule: HandoffCapsule, *, completed_head: str | None = None) -> None:
        """Only a turn already recorded as continuing may finish."""
        data = self._load()
        entry = self.read(capsule)
        if entry is None or entry["state"] != "continuing":
            raise StateError("handoff has no continuing turn")
        data[self.key(capsule)] = {**entry, "state": "done", "completed_head": completed_head}
        self._save(data)
