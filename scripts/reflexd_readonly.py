"""Read-only wakes never ship code (security fix 15, 2026-10-17).

Quorus publishes an agent's worktree to the room's main branch after every
wake. A read-only turn (a non-owner sender, an answer-only turn, a manual
room's Codex / opencode / Gemini) relies on the harness to refuse writes, and
harnesses leak: Gemini's plan mode wrote and committed a file in a manual room,
and Quorus itself merged it to main (release sweep). So the daemon now checks
the worktree, not the harness:

* before a read-only wake, :func:`begin` records HEAD and ``git status`` in a
  marker file (it survives a crash, so the startup publish sweep sees it);
* afterwards, :func:`settle` compares. Anything changed is saved to
  ``refs/quorus/discarded/<agent>/<ts>`` and the worktree is reset; nothing
  from that turn is ever published.

Git runs with hooks disabled, as everywhere in the daemon: the agent can edit
tracked hooks in its worktree.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

DISCARDED_PREFIX = "refs/quorus/discarded"
_IDENTITY = {"GIT_AUTHOR_NAME": "Quorus", "GIT_AUTHOR_EMAIL": "agents@quorus.local",
             "GIT_COMMITTER_NAME": "Quorus", "GIT_COMMITTER_EMAIL": "agents@quorus.local"}


def _git(path: Path, *args: str, env: dict[str, str] | None = None) -> str:
    out = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=path, capture_output=True,
        text=True, timeout=120, env=env, check=True)
    return out.stdout


def snapshot(path: Path) -> dict[str, str]:
    """HEAD plus every change git can see, untracked files included."""
    return {"head": _git(path, "rev-parse", "HEAD").strip(),
            "status": _git(path, "status", "--porcelain=v1", "--untracked-files=all")}


def _marker(marker_dir: Path, path: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(path.resolve()))[-150:]
    return marker_dir / f"{safe}.json"


def begin(marker_dir: Path, path: Path) -> dict[str, str]:
    """Snapshot *path* and persist it until :func:`settle` runs."""
    snap = {**snapshot(path), "path": str(path.resolve())}
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = _marker(marker_dir, path)
    marker.write_text(json.dumps(snap), encoding="utf-8")
    os.chmod(marker, 0o600)
    return snap


def pending(marker_dir: Path, path: Path) -> dict[str, str] | None:
    """The snapshot of an unfinished read-only wake on *path* (a crash), if any."""
    try:
        return json.loads(_marker(marker_dir, path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def settle(marker_dir: Path, path: Path, snap: dict[str, str], agent: str,
           now: float | None = None) -> str | None:
    """End a read-only wake. If it changed anything, keep the changes under a
    discarded ref, put the worktree back, and return the ref; else None.

    Fails closed: if git fails, the marker stays, so neither the caller nor
    the startup sweep publishes this worktree until a later settle succeeds.
    """
    after = snapshot(path)
    ref = None
    if after != {"head": snap["head"], "status": snap["status"]}:
        _git(path, "add", "-A")
        tree = _git(path, "write-tree").strip()
        commit = _git(path, "commit-tree", tree, "-p", after["head"], "-m",
                      f"changes from a read-only turn by {agent} (discarded, never published)",
                      env={**os.environ, **_IDENTITY}).strip()
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now or time.time()))
        ref = f"{DISCARDED_PREFIX}/{re.sub(r'[^A-Za-z0-9._-]', '_', agent)}/{stamp}"
        _git(path, "update-ref", ref, commit)
        _git(path, "reset", "-q", "--hard", snap["head"])
        _git(path, "clean", "-fdq")
    try:
        _marker(marker_dir, path).unlink()
    except OSError:
        pass
    return ref


def is_read_only(harness: str, mode: str) -> bool:
    """A turn that must not change the repo. Manual Claude asks before each
    write (approvals), so its approved writes are real work; the other
    harnesses have no approval path and run manual turns read-only."""
    return mode == "readonly" or (mode == "manual" and harness != "claude")


def notice(ref: str) -> str:
    return f"⚠ a read-only turn changed files; discarded, not published. See {ref}"
