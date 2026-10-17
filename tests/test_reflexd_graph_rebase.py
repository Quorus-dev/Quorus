"""Generated tracked graphs must not obstruct sync or publication rebases."""

import os
import subprocess

import pytest

from tests.test_reflexd_wake_contract import _git_repo, wake


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.mark.parametrize("operation", ["sync", "publish"])
@pytest.mark.parametrize("main_changes_graph", [False, True])
@pytest.mark.parametrize("refresh", ["modified", "deleted", "staged"])
def test_graph_refresh_does_not_block_rebase(tmp_path, operation, main_changes_graph, refresh):
    repo = _git_repo(tmp_path / "repo")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.test")
    git(repo, "config", "core.excludesfile", os.devnull)
    graph = "graphify-out/GRAPH REPORT.md"
    (repo / graph).parent.mkdir()
    (repo / graph).write_text("original")
    git(repo, "add", "-f", graph)
    git(repo, "commit", "-qm", "seed graph")
    wt = wake.agent_worktree(repo, "a-codex")
    if operation == "publish":
        (wt.path / "feature.py").write_text("value = 1\n")
        assert wake.commit_all(wt, "a-codex", "feat: add value")
    (repo / "main.txt").write_text("main advanced")
    if main_changes_graph:
        (repo / graph).write_text("upstream graph")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "advance main")
    main_head = git(repo, "rev-parse", "HEAD")
    (wt.path / graph).write_text("local generated refresh")
    if refresh == "deleted":
        (wt.path / graph).unlink()
    elif refresh == "staged":
        git(wt.path, "add", "-f", graph)
    assert not wake.worktree_dirty(wt)

    if operation == "sync":
        wake.sync_worktree(wt)
        assert git(wt.path, "rev-parse", "HEAD") == main_head
    else:
        status = wake.publish_worktree(wt, "a-codex")
        assert "published 1 commit" in status
        assert git(wt.path, "rev-parse", "HEAD^") == main_head
        assert (repo / "feature.py").exists()
        assert git(repo, "rev-list", "--merges", f"{main_head}..HEAD") == ""
    assert (wt.path / graph).read_text() == (
        "upstream graph" if main_changes_graph else "original"
    )
    assert git(wt.path, "status", "--porcelain") == ""
