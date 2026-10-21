"""Publication protection around adoption, including failed proof and recovery."""

from __future__ import annotations

import json
import os
from pathlib import Path

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_state import Journal, StateError, fingerprint
from quorus.runtime.worktree_hold import marker_path


class PublicationHold:
    """Use the SAME unfinished marker that reflexd's publish sweep already checks."""

    def __init__(self, root: Path, journal: Journal, capsule: HandoffCapsule):
        if journal.hold_dir is None:
            raise StateError("daemon publication hold directory required")
        self.path = marker_path(journal.hold_dir, root)
        self.root, self.capsule = root, capsule

    def owned(self) -> bool:
        try:
            value = json.loads(self.path.read_text())
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as exc:
            raise StateError("publication hold unreadable; owner needed") from exc
        return (isinstance(value, dict) and value.get("handoff") == fingerprint(self.capsule)
                and value.get("path") == str(self.root.resolve()))

    def acquire(self, branch: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Never replace another turn's hold, including a race after inspection.
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not self.owned():
                raise StateError("receiver already held by another turn")
            return
        with os.fdopen(fd, "w") as stream:
            json.dump({"path": str(self.root.resolve()), "branch": f"{branch}-unfinished",
                       "handoff": fingerprint(self.capsule)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        self._sync_dir()

    def _sync_dir(self) -> None:
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def release(self) -> None:
        if not self.owned():
            raise StateError("publication hold ownership changed")
        self.path.unlink()
        self._sync_dir()


def restore(git: Git, journal: Journal, capsule: HandoffCapsule,
            hold: PublicationHold) -> bool:
    """CAS back to prev only with untouched index/worktree. Otherwise retain hold.

    Also recovers a crash between the restoring ref move and read-tree: prev may
    be HEAD while the index still names the adopted tree. No new work is removed.
    The caller records the terminal state BEFORE releasing the hold.
    """
    entry = journal.read(capsule)
    if entry is None or not entry.get("prev") or not hold.owned():
        return False
    prev, branch = entry["prev"], entry.get("branch")
    try:
        if git.text("symbolic-ref", "HEAD") != f"refs/heads/{branch}":
            return False
        current = git.text("rev-parse", "HEAD")
        if current not in (prev, capsule.repo.head):
            return False
        git.run("diff-files", "--quiet", "--")
        if git.run("ls-files", "--others", "--exclude-standard", "-z"):
            return False
        if git.text("write-tree") not in (git.text("rev-parse", f"{prev}^{{tree}}"),
                                         capsule.repo.tree):
            return False
        if current != prev:
            git.run("update-ref", "-m", "quorus restore handoff", f"refs/heads/{branch}",
                    prev, capsule.repo.head)
        if (git.text("symbolic-ref", "HEAD") != f"refs/heads/{branch}"
                or git.text("rev-parse", "HEAD") != prev):
            return False
        git.run("read-tree", "-u", "--reset", "HEAD")
        return git.text("rev-parse", "HEAD") == prev and git.clean()
    except StateError:
        return False
