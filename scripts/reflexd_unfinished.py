"""A turn that runs out of time never publishes (overseer, 0.5.1 triage).

A wake cut off by its time limit used to publish whatever it had committed to
the room's branch. Gemini's half-written CHANGELOG went to main that way and
deleted 86 lines (c36101ec). Now every writable turn runs under a
:func:`hold`, a marker that stops every publish of that worktree, later wakes
and the startup sweep alike. A turn that doesn't finish cleanly (timed out,
errored, a vendor failure) keeps it, and :func:`park` copies the branch to
``quorus/<agent>-unfinished`` for review. Only a turn that finishes cleanly
runs :func:`clear`; that turn is the agent finishing its work ("mention me to
finish"), so it publishes everything.

It fails closed (codex, reviews of 87d7c3a5 and 97d81fb8): the daemon takes
the hold before the harness even starts, and a turn whose hold can't be
written doesn't run. So a crash, a failed park or a failed retry all leave it
in place, across restarts too. Git runs with hooks disabled.
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


def hold(marker_dir: Path, wt) -> None:
    """Block publishing *wt* until a turn finishes cleanly. Written first, so
    nothing that fails afterwards can leave the worktree publishable."""
    marker = _marker(marker_dir, wt.path)
    if marker.exists():
        return  # an earlier unfinished turn's hold: keep it
    marker_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=marker_dir, prefix=".unfinished-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"path": str(Path(wt.path).resolve()),
                       "branch": branch_for(wt.branch)}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, marker)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def park(marker_dir: Path, wt, participant: str, message: str) -> str | None:
    """Under a :func:`hold`: commit what the turn left uncommitted and copy the
    branch to ``<branch>-unfinished``. Returns that branch, or None when there
    is nothing unpublished (then the hold is lifted: nothing to protect)."""
    from reflexd_wake import commit_all, merge_in_progress

    if not merge_in_progress(wt):
        commit_all(wt, participant, message)
    ahead = _git(wt.repo, "rev-list", "--count", f"{wt.main}..{wt.branch}")
    if ahead.returncode != 0:
        raise OSError(f"couldn't compare {wt.branch} with {wt.main}")
    if ahead.stdout.strip() in ("", "0"):
        _marker(marker_dir, wt.path).unlink(missing_ok=True)
        return None
    unfinished = branch_for(wt.branch)
    if _git(wt.repo, "branch", "-f", unfinished, wt.branch).returncode != 0:
        raise OSError(f"couldn't create {unfinished}")
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
