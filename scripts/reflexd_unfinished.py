"""A turn that runs out of time never publishes (overseer, 0.5.1 triage).

A wake cut off by its time limit used to publish whatever it had committed to
the room's branch. Gemini's half-written CHANGELOG went to main that way and
deleted 86 lines (c36101ec). Now :func:`park` copies the branch to
``quorus/<agent>-unfinished`` for review and leaves a marker, and nothing
publishes that worktree (neither later wakes nor the startup sweep) until a
turn finishes cleanly and :func:`clear` removes it. That turn is the agent
finishing its work ("mention me to finish"), so it publishes everything.

The marker survives a crash, like the read-only marker, so a restart never
publishes the cut-off work either. Git runs with hooks disabled.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

SUFFIX = "-unfinished"


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=path,
                          capture_output=True, text=True, timeout=120)


def _marker(marker_dir: Path, path: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(Path(path).resolve()))[-150:]
    return marker_dir / f"{safe}.json"


def branch_for(branch: str) -> str:
    return f"{branch}{SUFFIX}"


def pending(marker_dir: Path, path: Path) -> bool:
    return _marker(marker_dir, path).exists()


def park(marker_dir: Path, wt, participant: str, message: str) -> str | None:
    """After a cut-off turn: commit what it left uncommitted, copy the branch
    to ``<branch>-unfinished`` and block publishing. Returns that branch, or
    None when the turn left nothing new (then nothing is blocked)."""
    from reflexd_wake import commit_all, merge_in_progress

    if not merge_in_progress(wt):
        commit_all(wt, participant, message)
    ahead = _git(wt.repo, "rev-list", "--count", f"{wt.main}..{wt.branch}")
    if ahead.returncode != 0 or ahead.stdout.strip() in ("", "0"):
        return None
    unfinished = branch_for(wt.branch)
    if _git(wt.repo, "branch", "-f", unfinished, wt.branch).returncode != 0:
        raise OSError(f"couldn't create {unfinished}")
    marker_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=marker_dir, prefix=".unfinished-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"path": str(Path(wt.path).resolve()), "branch": unfinished}, fh)
    os.replace(tmp, _marker(marker_dir, wt.path))
    return unfinished


def clear(marker_dir: Path, wt) -> None:
    """A turn finished cleanly: publishing may resume. The review copy is
    deleted once the agent's branch contains it (the finished work does)."""
    marker = _marker(marker_dir, wt.path)
    if not marker.exists():
        return
    unfinished = branch_for(wt.branch)
    if _git(wt.repo, "merge-base", "--is-ancestor", unfinished, wt.branch).returncode == 0:
        _git(wt.repo, "branch", "-D", unfinished)
    marker.unlink(missing_ok=True)


def notice(unfinished: str, last_step: str = "") -> str:
    where = f" while {last_step}," if last_step else ""
    return (f"⚠ ran out of time{where} before finishing. Its commits are on "
            f"{unfinished}, not published: review and merge, or mention me to finish.")
