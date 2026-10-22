"""Adopt an immutable commit without recreating it or discarding receiver work."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime import commit_evidence as evidence
from quorus.runtime.handoff_state import Journal, StateError


class Declined(StateError):
    """Receiver's existing work wins; choose another candidate."""


class Git:
    """One bounded resolve/adoption budget; no hooks, external diff or replacements."""

    def __init__(self, root: Path, timeout: float = 10):
        self.root, self.deadline = root, time.monotonic() + timeout

    def run(self, *args: str) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise StateError("adoption git deadline exceeded")
        argv = ["git", "--no-replace-objects"]
        for item in evidence._SAFE_CONFIG:
            argv += ["-c", item]
        try:
            code, output, cut = evidence.collect([*argv, *args], self.root, 1048576,
                                                 timeout=remaining)
        except (OSError, subprocess.SubprocessError) as exc:
            raise StateError("adoption git unavailable") from exc
        if code != 0 or cut:
            raise StateError("adoption git command failed")
        return output

    def text(self, *args: str) -> str:
        return self.run(*args).decode().strip()

    def clean(self) -> bool:
        """No tracked change at all, and no untracked file except generated leftovers
        (__pycache__ and the like), which the daemon never commits either."""
        from quorus.runtime.generated_files import EXCLUDES

        return not (self.run("status", "--porcelain=v1", "-z", "--untracked-files=no")
                    or self.run("ls-files", "--others", "--exclude-standard", "-z",
                                "--", ".", *EXCLUDES))


def adopt(git: Git, journal: Journal, capsule: HandoffCapsule, *,
          branch: str, integration: str, held: bool, recovering: bool = False) -> None:
    """Caller holds the journal lock and has verified the receiver's relay claim.

    On recovery, the index must still represent the recorded old tree (or the new
    one), and tracked/untracked files must be unchanged relative to that index.
    Otherwise read-tree would destroy new work: leave it intact and ask the owner.
    """
    from quorus.runtime.handoff_hold import PublicationHold

    hold = PublicationHold(git.root, journal, capsule)
    ours = hold.owned()
    if held and not (recovering and ours):
        raise Declined("receiver has an unfinished turn")
    if hold.path.exists() and not ours:
        raise Declined("receiver has another turn's publication hold")
    expected = f"refs/heads/{branch}"
    if git.text("symbolic-ref", "HEAD") != expected:
        raise StateError("receiver branch is not checked out")
    if git.text("rev-parse", f"{capsule.repo.head}^{{tree}}") != capsule.repo.tree:
        raise StateError("immutable snapshot tree mismatch")
    entry = journal.read(capsule)
    if entry is None:
        raise StateError("handoff not recorded")
    current = git.text("rev-parse", "HEAD")
    if recovering and entry["state"] in {"adopting", "adopted", "verified"}:
        prev = entry.get("prev")
        if not isinstance(prev, str) or not evidence.FULL_SHA_RE.fullmatch(prev):
            raise StateError("adoption previous commit missing")
        if current not in (prev, capsule.repo.head):
            raise StateError("adoption interrupted: branch moved")
        # Worktree vs index must be clean even if HEAD moved before read-tree.
        git.run("diff-files", "--quiet", "--")
        if git.run("ls-files", "--others", "--exclude-standard", "-z"):
            raise StateError("adoption interrupted: new untracked work")
        index = git.text("write-tree")
        if index not in (git.text("rev-parse", f"{prev}^{{tree}}"), capsule.repo.tree):
            raise StateError("adoption interrupted: index changed")
    else:
        if not git.clean():
            raise Declined("receiver has dirty work")
        # Not just 'ahead count': divergent or unrelated work must also survive.
        if git.text("rev-list", "--count", f"{integration}..{current}") != "0":
            raise Declined("receiver has unpublished commits")
        git.run("merge-base", "--is-ancestor", current, integration)
        prev = current
        journal.write(capsule, "adopting", prev=prev, branch=branch)
    # The existing daemon sweep sees this marker BEFORE the ref can move. Keep it
    # through proof and launch; only rollback or a normal completed turn lifts it.
    hold.acquire(branch)
    if current == prev:
        git.run("update-ref", "-m", "quorus adopt handoff", expected, capsule.repo.head, prev)
    if (git.text("symbolic-ref", "HEAD") != expected
            or git.text("rev-parse", "HEAD") != capsule.repo.head):
        raise StateError("adoption interrupted: HEAD changed")
    git.run("read-tree", "-u", "--reset", "HEAD")
    if git.text("rev-parse", "HEAD") != capsule.repo.head or not git.clean():
        raise StateError("adoption incomplete")
    journal.write(capsule, "adopted", prev=prev, branch=branch)
