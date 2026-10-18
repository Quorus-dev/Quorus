"""Read-only wakes never ship code (security fix 15, 2026-10-17).

Quorus publishes an agent's worktree to the room's main branch after every
wake. A read-only turn (a non-owner sender, an answer-only turn, a manual
room's Codex / opencode / Gemini) relies on the harness to refuse writes, and
harnesses leak: Gemini's plan mode wrote and committed a file in a manual room,
and Quorus itself merged it to main (release sweep). So the daemon now checks
the worktree, not the harness:

* before a read-only wake, :func:`begin` records HEAD and a tree of the
  worktree's exact contents (tracked and untracked) in a marker file, written
  atomically; it survives a crash, so the startup publish sweep sees it;
* afterwards, :func:`settle` compares. Anything changed is saved to
  ``refs/quorus/discarded/<agent>/<ts>`` and the worktree is put back exactly
  as it was before the turn, prior uncommitted work included.

It fails closed (codex, review of f09377ed): a marker that can't be read is an
error, not "no marker"; a new turn never overwrites one; and if settling fails
the marker stays, so nothing publishes the worktree until a human looks.

Git runs with hooks disabled, as everywhere in the daemon: the agent can edit
tracked hooks in its worktree.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

DISCARDED_PREFIX = "refs/quorus/discarded"
_IDENTITY = {"GIT_AUTHOR_NAME": "Quorus", "GIT_AUTHOR_EMAIL": "agents@quorus.local",
             "GIT_COMMITTER_NAME": "Quorus", "GIT_COMMITTER_EMAIL": "agents@quorus.local"}


class MarkerError(RuntimeError):
    """A read-only marker exists but can't be trusted: publishing must stop."""


class SnapshotError(RuntimeError):
    """The worktree couldn't be snapshotted before a read-only turn, so the
    turn must not run: its writes could not be told apart afterwards."""


def _git(path: Path, *args: str, env: dict[str, str] | None = None) -> str:
    out = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=path, capture_output=True,
        text=True, timeout=300, env=env, check=True)
    return out.stdout


def _content_tree(path: Path) -> str:
    """A tree object of exactly what is on disk (tracked and untracked, not
    ignored), built in a scratch index so the real one is untouched. Seeded
    from the real index so unchanged files aren't re-hashed."""
    real_index = Path(_git(path, "rev-parse", "--path-format=absolute",
                           "--git-path", "index").strip())
    with tempfile.TemporaryDirectory() as scratch:
        index = Path(scratch) / "index"
        if real_index.exists():
            shutil.copyfile(real_index, index)
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        _git(path, "add", "-A", env=env)
        return _git(path, "write-tree", env=env).strip()


def snapshot(path: Path) -> dict[str, str]:
    """HEAD plus the exact contents of the worktree. Contents, not ``git
    status``: an edit to a file that was already modified leaves status as
    it was (codex, review of f09377ed)."""
    return {"head": _git(path, "rev-parse", "HEAD").strip(), "tree": _content_tree(path)}


def _marker(marker_dir: Path, path: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(path.resolve()))[-150:]
    return marker_dir / f"{safe}.json"


def begin(marker_dir: Path, path: Path) -> dict[str, str]:
    """Snapshot *path* and persist it until :func:`settle` runs. Refuses to
    replace an unresolved marker: that is recovery state for a turn that may
    have leaked writes, and overwriting it would bless them."""
    marker = _marker(marker_dir, path)
    if marker.exists():
        raise MarkerError(f"an earlier read-only turn on {path} is unresolved ({marker})")
    snap = {**snapshot(path), "path": str(path.resolve())}
    marker_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=marker_dir, prefix=".marker-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snap, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, marker)  # atomic: never a half-written marker
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return snap


def pending(marker_dir: Path, path: Path) -> dict[str, str] | None:
    """The snapshot of an unfinished read-only wake on *path* (a crash), None
    if there is none. Raises :class:`MarkerError` if one exists but can't be
    read or parsed: that must block publishing, not allow it."""
    marker = _marker(marker_dir, path)
    if not marker.exists():
        return None
    try:
        snap = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MarkerError(f"unreadable read-only marker {marker}: {exc}") from exc
    if not (isinstance(snap, dict) and isinstance(snap.get("head"), str)
            and isinstance(snap.get("tree"), str)):
        raise MarkerError(f"invalid read-only marker {marker}")
    return snap


def _restore(path: Path, snap: dict[str, str]) -> None:
    """Put the worktree back exactly as it was before the turn: HEAD, and any
    uncommitted work the owner had there (left unstaged, as it was)."""
    _git(path, "reset", "-q", "--hard", snap["head"])
    _git(path, "clean", "-fdq")
    head_tree = _git(path, "rev-parse", f"{snap['head']}^{{tree}}").strip()
    if snap["tree"] == head_tree:
        return
    _git(path, "checkout", snap["tree"], "--", ".")
    gone = _git(path, "diff", "--name-only", "-z", "--diff-filter=D",
                snap["head"], snap["tree"]).split("\0")
    for rel in filter(None, gone):  # deleted before the turn: stay deleted
        (path / rel).unlink(missing_ok=True)
    _git(path, "reset", "-q")  # back to unstaged, as the owner left it


def settle(marker_dir: Path, path: Path, snap: dict[str, str], agent: str,
           now: float | None = None) -> str | None:
    """End a read-only wake. If it changed anything, keep the changes under a
    discarded ref, put the worktree back as it was, and return the ref; else
    None. If git fails, the marker stays and the error propagates."""
    after = snapshot(path)
    ref = None
    if after != {"head": snap["head"], "tree": snap["tree"]}:
        commit = _git(path, "commit-tree", after["tree"], "-p", after["head"], "-m",
                      f"changes from a read-only turn by {agent} (discarded, never published)",
                      env={**os.environ, **_IDENTITY}).strip()
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now or time.time()))
        ref = f"{DISCARDED_PREFIX}/{re.sub(r'[^A-Za-z0-9._-]', '_', agent)}/{stamp}"
        _git(path, "update-ref", ref, commit)
        _restore(path, snap)
    _marker(marker_dir, path).unlink(missing_ok=True)
    return ref


def is_read_only(harness: str, mode: str) -> bool:
    """A turn that must not change the repo. Manual Claude asks before each
    write (approvals), so its approved writes are real work; the other
    harnesses have no approval path and run manual turns read-only."""
    return mode == "readonly" or (mode == "manual" and harness != "claude")


def notice(ref: str) -> str:
    return f"⚠ a read-only turn changed files; discarded, not published. See {ref}"
