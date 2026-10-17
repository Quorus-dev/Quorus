"""Daemon commits keep short subjects and leave generated graphs alone."""

import os
import subprocess

import pytest

from tests.test_reflexd_wake_contract import _git_repo, wake


@pytest.mark.parametrize("reply, expected", [
    ("✅ **Shipped** `x`", "chore: Shipped x"),
    ("fix: report raised harness failures", "fix: report raised harness failures"),
    ("fix: x", "fix: x"),
    ("fix: " + "a" * 45, "fix: " + "a" * 45),
    ("fix: " + "a" * 45 + " extra", "fix: " + "a" * 45),
    ("feat(cli)!: change defaults", "feat(cli)!: change defaults"),
    ("Added feature.txt with the done marker; tests green",
     "chore: Added feature.txt with the done marker;"),
    ("fix: " + "a" * 100, "chore: update agent work"),
    ("", "chore: update agent work"),
    ("(quorus) published 1 commit\nfix: repair joins", "fix: repair joins"),
    ("fix:   normalize\tspacing", "fix: normalize spacing"),
])
def test_short_conventional_subject(reply, expected):
    subject = wake.commit_message_from(reply, "a-codex")
    assert subject == expected
    assert len(subject) <= 50


@pytest.mark.parametrize("tracked", [False, True])
def test_graph_refresh_is_not_work_or_committed(tmp_path, tracked):
    repo = _git_repo(tmp_path / "repo")
    wt = wake.agent_worktree(repo, "a-codex")
    subprocess.run(["git", "config", "core.excludesfile", os.devnull], cwd=wt.path, check=True)
    graph = wt.path / "graphify-out" / "nested" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text("original")
    if tracked:
        subprocess.run(["git", "add", "."], cwd=wt.path, check=True)
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@t",
                        "commit", "-qm", "seed graph"], cwd=wt.path, check=True)
    graph.write_text("refreshed")
    assert not wake.worktree_dirty(wt)
    assert not wake.commit_all(wt, "a-codex", "chore: refresh graph")

    (wt.path / "feature.py").write_text("value = 1\n")
    assert wake.commit_all(wt, "a-codex", "feat: add value")
    files = subprocess.check_output(
        ["git", "show", "--name-only", "--format=", "HEAD"], cwd=wt.path, text=True,
    ).split()
    assert files == ["feature.py"]
    assert graph.read_text() == "refreshed"
