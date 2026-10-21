"""F5 matches the fix on main by patch-id, not by the SHA the ✅ named (overseer, 2026-10-21).

Through the daemon's real publish: when main moves under the agent, publish rebases its
branch and main gets a new SHA. The exact-SHA check the old F5 used then never
succeeds, which is the PR #15/main-run flake shape; `landed` still finds the fix.
"""

import os
import subprocess

from tests.firstrun.test_f5_fix_lands import _landed as landed
from tests.test_reflexd_wake_contract import _git_repo, wake


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _repo(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    for k, v in (("user.name", "test"), ("user.email", "t@example.test"),
                 ("core.excludesfile", os.devnull)):
        git(repo, "config", k, v)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-qm", "calc with a bug")
    return repo


def test_main_moving_mid_run_gives_main_a_new_sha_that_landed_still_finds(tmp_path):
    repo = _repo(tmp_path)
    wt = wake.agent_worktree(repo, "alice-codex")
    (wt.path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(wt.path, "commit", "-qam", "fix: add() returns the sum")
    named = git(wt.path, "rev-parse", "HEAD")  # what the agent's ✅ names
    (repo / "notes.txt").write_text("someone else shipped\n")  # main moves mid-run
    git(repo, "add", "notes.txt")
    git(repo, "commit", "-qm", "docs: another agent's commit")

    assert "published 1 commit" in wake.publish_worktree(wt, "alice-codex")
    on_main = git(repo, "rev-parse", "main")
    assert on_main != named
    assert subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", named,
                           "main"]).returncode != 0  # the old check waits forever here
    assert landed(repo, named) == on_main
    assert "return a + b" in git(repo, "show", "main:calc.py")


def test_a_reapplied_hold_is_found_and_a_different_change_is_not(tmp_path):
    repo = _repo(tmp_path)
    git(repo, "checkout", "-qb", "side")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "commit", "-qam", "fix: add() returns the sum")
    held = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    (repo / "x.txt").write_text("x\n")
    git(repo, "add", "x.txt")
    git(repo, "commit", "-qm", "move main")
    git(repo, "cherry-pick", held)  # what `reapply held` does
    assert landed(repo, held) == git(repo, "rev-parse", "main")

    git(repo, "checkout", "-qb", "other", "main~2")
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    git(repo, "commit", "-qam", "fix: a different fix")
    assert landed(repo, git(repo, "rev-parse", "HEAD")) is None
    assert landed(repo, "0" * 40) is None  # an unknown SHA is "not landed", not a crash
