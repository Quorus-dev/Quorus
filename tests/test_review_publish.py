"""Review gate slice 2: publish exactly the approved sha, crash-safely
(docs/plans/review-gate.md, Codex GO on a1f25392)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from quorus.runtime import review_publish as rp

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
BRANCH = "quorus/x"


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True,
                          text=True, check=True)
    return done.stdout.strip()


def commit(cwd: Path, name: str, text: str) -> str:
    (cwd / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", f"add {name}")
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    base = commit(repo, "a.txt", "a\n")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt))
    commit(wt, "b.txt", "b\n")
    tip = commit(wt, "c.txt", "c\n")
    return repo, wt, base, tip, tmp_path / "run"


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def assert_consistent(repo: Path) -> None:
    """HEAD, the index and the files agree."""
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_publishes_exactly_the_approved_sha(setup):
    repo, _wt, base, tip, run = setup
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert (res.status, res.before, res.sha) == ("published", base, tip)
    assert head(repo) == tip and (repo / "c.txt").read_text() == "c\n"
    assert_consistent(repo)
    assert not rp.journal_path(run, repo).exists()


def test_check_reason_holds(setup):
    repo, _wt, base, tip, run = setup
    res = rp.publish_approved(run, repo, "main", BRANCH, tip, check=lambda: "blocked")
    assert (res.status, res.detail) == ("held", "blocked")
    assert head(repo) == base


def test_commit_after_go_is_not_published(setup):
    repo, wt, base, tip, run = setup
    commit(wt, "d.txt", "unreviewed\n")
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert res.status == "not_tip" and head(repo) == base


def test_commit_racing_the_merge_stays_on_the_branch(setup, monkeypatch):
    repo, wt, _base, tip, run = setup
    real = rp._ff

    def racing(r, sha):
        commit(wt, "d.txt", "unreviewed\n")  # the agent commits mid-publish
        return real(r, sha)

    monkeypatch.setattr(rp, "_ff", racing)
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert res.status == "published" and head(repo) == tip
    assert not (repo / "d.txt").exists()
    assert git(repo, "rev-parse", BRANCH) != tip


def test_main_moved_is_behind_and_never_rebased(setup):
    repo, wt, _base, tip, run = setup
    moved = commit(repo, "z.txt", "z\n")
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert res.status == "behind" and head(repo) == moved
    assert git(wt, "rev-parse", "HEAD") == tip  # the branch is untouched


def test_main_moving_during_the_merge_publishes_nothing(setup, monkeypatch):
    repo, _wt, _base, tip, run = setup
    real = rp._ff
    moved = {}

    def racing(r, sha):
        moved["sha"] = commit(repo, "z.txt", "z\n")  # another writer wins
        return real(r, sha)

    monkeypatch.setattr(rp, "_ff", racing)
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert res.status == "failed" and head(repo) == moved["sha"]
    assert_consistent(repo)
    assert not rp.journal_path(run, repo).exists()


@pytest.mark.parametrize("dirty", ["edit", "staged"])
def test_dirty_checkout_publishes_nothing(setup, dirty):
    repo, _wt, base, tip, run = setup
    (repo / "a.txt").write_text("human edit\n")
    if dirty == "staged":
        git(repo, "add", "a.txt")
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert res.status == "dirty" and head(repo) == base
    assert (repo / "a.txt").read_text() == "human edit\n"
    assert not rp.journal_path(run, repo).exists()


def test_checkout_on_another_branch_publishes_nothing(setup):
    repo, _wt, base, tip, run = setup
    git(repo, "checkout", "-q", "-b", "feature")
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert res.status == "wrong_branch"
    assert git(repo, "rev-parse", "main") == base


def test_publishes_serialize_on_the_repo_lock(setup):
    repo, _wt, _base, tip, run = setup
    order = []

    def hold():
        with rp.repo_lock(run, repo):
            order.append("held")
            time.sleep(0.3)
            order.append("released")

    t = threading.Thread(target=hold)
    t.start()
    while not order:
        time.sleep(0.01)
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    order.append(res.status)
    t.join()
    assert order == ["held", "released", "published"]


def test_two_publishes_of_one_go_land_once(setup):
    repo, _wt, _base, tip, run = setup
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        rp.publish_approved(run, repo, "main", BRANCH, tip).status)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # The second sees main already at the tip: one publish, one note.
    assert sorted(results) == ["already", "published"]
    assert head(repo) == tip


# ── interrupted publishes ───────────────────────────────────────────────


def journal(run: Path, repo: Path, src: str, dst: str) -> Path:
    path = rp.journal_path(run, repo)
    run.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"from": src, "to": dst, "main": "main", "branch": BRANCH}))
    return path


def test_crash_before_the_merge(setup):
    repo, _wt, base, tip, run = setup
    path = journal(run, repo, base, tip)
    assert rp.recover(run, repo, "main") is None
    assert not path.exists() and head(repo) == base
    assert_consistent(repo)


def test_crash_after_files_moved_before_the_ref(setup):
    repo, _wt, base, tip, run = setup
    path = journal(run, repo, base, tip)
    git(repo, "read-tree", "-m", "-u", base, tip)  # files + index at tip, HEAD at base
    assert (repo / "c.txt").exists() and head(repo) == base
    assert rp.recover(run, repo, "main") is None
    assert not path.exists() and head(repo) == base
    assert not (repo / "c.txt").exists()
    assert_consistent(repo)
    # and the approved publish then re-runs cleanly
    assert rp.publish_approved(run, repo, "main", BRANCH, tip).status == "published"


def test_crash_after_the_ref_moved(setup):
    repo, _wt, base, tip, run = setup
    path = journal(run, repo, base, tip)
    git(repo, "merge", "--ff-only", "-q", tip)
    res = rp.recover(run, repo, "main")
    assert res is not None and (res.status, res.sha) == ("published", tip)
    assert not path.exists()
    # publish_approved of the same sha just reports it once
    journal(run, repo, base, tip)
    again = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert again.status == "published" and again.detail == "finished before a restart"


def test_human_edit_after_a_crash_is_stuck(setup):
    repo, _wt, base, tip, run = setup
    path = journal(run, repo, base, tip)
    git(repo, "read-tree", "-m", "-u", base, tip)
    (repo / "a.txt").write_text("human edit\n")
    res = rp.recover(run, repo, "main")
    assert res is not None and res.status == "stuck"
    assert path.exists() and (repo / "a.txt").read_text() == "human edit\n"
    # and nothing else publishes until the owner settles it
    assert rp.publish_approved(run, repo, "main", BRANCH, tip).status == "stuck"
    assert head(repo) == base


def test_unreadable_journal_is_stuck(setup):
    repo, _wt, base, tip, run = setup
    run.mkdir()
    rp.journal_path(run, repo).write_text("{bad")
    assert rp.publish_approved(run, repo, "main", BRANCH, tip).status == "stuck"
    assert head(repo) == base


def test_failed_merge_leaves_no_journal(setup, monkeypatch):
    repo, _wt, base, tip, run = setup
    monkeypatch.setattr(rp, "_ff", lambda r, sha: subprocess.CompletedProcess(
        [], 1, "", "boom"))
    res = rp.publish_approved(run, repo, "main", BRANCH, tip)
    assert (res.status, res.detail) == ("failed", "boom") and head(repo) == base
    assert not rp.journal_path(run, repo).exists()
