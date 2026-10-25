"""Review gate, slice 2: publish exactly the approved sha, crash-safely.

Plan: docs/plans/review-gate.md (Codex GO on a1f25392). ``publish_worktree``
rebases and merges the branch *name*, so a commit made after the GO, or a
rebase onto a moved main, would reach main unreviewed. :func:`publish_approved`
never rebases: under one lock per repo it re-checks, journals, and
fast-forwards the main checkout to the approved sha itself, with git's own
checkout-aware ``merge --ff-only`` (never a bare ``update-ref``, which would
move main and leave the checkout's files and index stale).

A journal left by a crash is settled before anything else is published:
finished (report it), never landed (put the checkout back to ``from``,
which is safe only when the index and files are exactly ``from`` or ``to``),
or anything else (keep the journal, publish nothing, tell the owner).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from quorus.runtime import file_lock


@dataclass
class Result:
    status: str  # published|already|held|not_tip|behind|wrong_branch|dirty|failed|stuck
    detail: str = ""
    before: str = ""
    sha: str = ""
    recovered: "Result | None" = None  # an earlier publish found finished at startup


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # Never run hooks from the daemon (same rule as reflexd_wake._git): a
    # post-merge hook would fire in the human's main checkout.
    return subprocess.run(["git", "-c", f"core.hooksPath={os.devnull}", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          timeout=900, check=False)


def _out(*args: str, cwd: Path) -> str:
    done = _git(*args, cwd=cwd)
    return done.stdout.strip() if done.returncode == 0 else ""


def repo_identity(repo: Path) -> str:
    """The checkout's full canonical path: what a lock and journal belong to."""
    return os.path.realpath(repo)


def _name(runtime_dir: Path, repo: Path, suffix: str) -> Path:
    # A hash of the whole identity, never a sanitized or truncated path:
    # "a/b" and "a_b" sanitized to one name and shared a lock and journal
    # (codex, review of eed52d75).
    digest = hashlib.sha256(repo_identity(repo).encode("utf-8")).hexdigest()
    return runtime_dir / f"publish-{digest}.{suffix}"


def journal_path(runtime_dir: Path, repo: Path) -> Path:
    return _name(runtime_dir, repo, "json")


@contextmanager
def repo_lock(runtime_dir: Path, repo: Path) -> Iterator[None]:
    """Exclusive, blocking lock for every publish and gated rebase of *repo*."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(_name(runtime_dir, repo, "lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        file_lock.lock(fd)
        yield
    finally:
        os.close(fd)  # closing the fd releases the lock


def _write_journal(path: Path, data: dict[str, str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(data) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _clean(repo: Path) -> bool:
    done = _git("status", "--porcelain", "--untracked-files=no", cwd=repo)
    return done.returncode == 0 and not done.stdout.strip()


def _on_main(repo: Path, main: str) -> bool:
    return _out("symbolic-ref", "--short", "HEAD", cwd=repo) == main


def _matches(repo: Path, sha: str) -> bool:
    """The main checkout's index and tracked files are exactly *sha*'s tree."""
    return (_git("diff", "--cached", "--quiet", sha, cwd=repo).returncode == 0
            and _git("diff", "--quiet", sha, cwd=repo).returncode == 0)


def recover(runtime_dir: Path, repo: Path, main: str) -> Result | None:
    """Settle a journal a crash left behind. None when there is none or the
    publish never landed (the checkout is back at ``from``); a ``published``
    result when it finished but wasn't reported; ``stuck`` otherwise."""
    path = journal_path(runtime_dir, repo)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        src, dst, owner = str(data["from"]), str(data["to"]), data["repo"]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Result("stuck", f"publish journal {path} can't be read: {exc}")
    if owner != repo_identity(repo):
        # Never settle another checkout's publish against this one.
        return Result("stuck", f"publish journal {path} belongs to {owner!r}, not {repo}")
    head = _out("rev-parse", "HEAD", cwd=repo)
    on_main = _on_main(repo, main)
    if on_main and head and _clean(repo):
        # A clean checkout is never half-applied: the ref either reached
        # `to` (maybe with commits on top since) or the merge never landed.
        path.unlink()
        if head == dst or _git("merge-base", "--is-ancestor", dst, head,
                               cwd=repo).returncode == 0:
            return Result("published", "finished before a restart", src, dst)
        return None
    if on_main and head == src:
        if _matches(repo, dst):
            # Files and index moved, the ref didn't: put them back to `from`.
            # Nothing else is in the checkout, so no human edit is lost.
            if (_git("read-tree", "--reset", "-u", src, cwd=repo).returncode == 0
                    and _clean(repo)):
                path.unlink()
                return None
    return Result("stuck", (f"an interrupted publish of {dst[:12]} over {src[:12]} left "
                            f"the main checkout in another state; nothing published"),
                  src, dst)


def _ff(repo: Path, sha: str) -> subprocess.CompletedProcess[str]:
    """Checkout-aware fast-forward of the main checkout to exactly *sha*."""
    return _git("merge", "--ff-only", "--quiet", sha, cwd=repo)


def publish_approved(runtime_dir: Path, repo: Path, main: str, branch: str, sha: str,
                     check: Callable[[], str | None] = lambda: None) -> Result:
    """Fast-forward *main* in the checkout *repo* to the approved *sha*.

    *check* re-verifies the GO, the pin, any later block and the publish
    guard under the lock; a reason string holds the publish."""
    with repo_lock(runtime_dir, repo):
        earlier = recover(runtime_dir, repo, main)
        if earlier is not None and (earlier.status == "stuck" or earlier.sha == sha):
            return earlier
        result = _publish(runtime_dir, repo, main, branch, sha, check)
        result.recovered = earlier
        return result


def _publish(runtime_dir: Path, repo: Path, main: str, branch: str, sha: str,
             check: Callable[[], str | None]) -> Result:
    """The checks, journal and fast-forward; the caller holds the repo lock."""
    reason = check()
    if reason:
        return Result("held", reason, sha=sha)
    if _out("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=repo) != sha:
        return Result("not_tip", f"{branch} moved past {sha[:12]}", sha=sha)
    before = _out("rev-parse", "--verify", "-q", f"refs/heads/{main}", cwd=repo)
    if before == sha:  # a second handler for the same GO: nothing to post
        return Result("already", f"{sha[:12]} is already on {main}", before, sha)
    if not before or _git("merge-base", "--is-ancestor", before, sha,
                          cwd=repo).returncode != 0:
        return Result("behind", f"{main} moved; {sha[:12]} needs a rebase",
                      before, sha)
    if not _on_main(repo, main) or _out("rev-parse", "HEAD", cwd=repo) != before:
        return Result("wrong_branch", f"the main checkout isn't on {main}", before, sha)
    if not _clean(repo):
        return Result("dirty", f"the main checkout {repo} has uncommitted changes",
                      before, sha)
    path = journal_path(runtime_dir, repo)
    _write_journal(path, {"repo": repo_identity(repo), "from": before, "to": sha,
                          "main": main, "branch": branch})
    merged = _ff(repo, sha)
    if (merged.returncode == 0 and _out("rev-parse", "HEAD", cwd=repo) == sha
            and _clean(repo)):
        path.unlink()
        return Result("published", "", before, sha)
    settled = recover(runtime_dir, repo, main)
    if settled is not None:
        return settled
    return Result("failed", (merged.stderr or merged.stdout).strip()[:160], before, sha)
