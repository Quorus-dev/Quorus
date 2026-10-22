"""Task outcomes use real local histories, including rewritten commits."""

import os
import subprocess
from pathlib import Path

import pytest

from quorus.runtime.task_outcomes import evaluate

BASE = 1_700_000_000


@pytest.fixture
def repo(tmp_path):
    def git(*args, at=BASE):
        env = {**os.environ, "GIT_AUTHOR_DATE": f"{at} +0000", "GIT_COMMITTER_DATE": f"{at} +0000"}
        return (
            subprocess.check_output(["git", "-C", str(tmp_path), *args], env=env).decode().strip()
        )

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Test author")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "file").write_text("base\n")
    git("add", "file")
    git("commit", "-qm", "base")
    return tmp_path, git


def commit(repo, text, at=BASE + 10):
    path, git = repo
    (path / "file").write_text(text)
    git("add", "file")
    git("commit", "-qm", text, at=at)
    return git("rev-parse", "HEAD")


def task():
    return {
        "task": "ask",
        "agent": "codex",
        "vendor": "openai",
        "model": "model",
        "version": "1",
        "kind": "fix",
        "claimed_at": BASE,
        "deadline": BASE + 100,
        "read_at": BASE + 200,
    }


def event(kind, **fields):
    return {"type": kind, "at": BASE + 20, "task": "ask", **fields}


def read(repo, events, now=BASE + 300):
    return evaluate(repo[0], "main", task(), events, now)


def test_pending_kept_approval_and_rescue_usage(repo):
    sha = commit(repo, "new\n")
    rows = [
        event("publish", commits=[sha]),
        event("attempt", agent="codex", outcome="clean", wall_s=3, tokens=4),
        event("attempt", agent="rescuer", outcome="clean", wall_s=5, tokens=8),
        event("approval", sha=sha, actor="claude", vendor="anthropic", human=False),
    ]
    assert read(repo, rows, BASE + 100)["outcome"] == "pending"
    result = read(repo, rows)
    assert result["outcome"] == "kept"
    assert (result["wall_s"], result["tokens"], result["unknown_usage"]) == (8, 12, 0)
    assert result["approved"]
    assert result["agent"] == "codex"


def test_failed_abandoned_escalated_and_quota(repo):
    assert read(repo, [])["outcome"] == "abandoned"
    for outcome in ("errored", "timed_out", "could_not"):
        assert read(repo, [event("attempt", outcome=outcome)])["outcome"] == "failed"
    assert read(repo, [event("attempt", outcome="quota")])["outcome"] == "quota"
    assert (
        read(repo, [event("escalation", to="other"), event("attempt", outcome="quota")])["outcome"]
        == "escalated"
    )


def test_work_deadline_and_unknown_tokens(repo):
    sha = commit(repo, "new\n")
    result = read(
        repo,
        [
            event("publish", commits=[sha], at=BASE + 101),
            event("attempt", outcome="clean", tokens=None, wall_s=4),
            event("attempt", outcome="clean", tokens=10, wall_s=2),
        ],
    )
    assert result["outcome"] == "abandoned"
    assert result["commits_published"] == 0
    assert result["tokens"] is None and result["unknown_usage"] == 1
    assert result["wall_s"] == 6


def test_revert_author_reason_and_late_revert_excluded(repo):
    sha = commit(repo, "new\n")
    repo[1]("revert", "--no-edit", sha, at=BASE + 150)
    result = read(repo, [event("publish", commits=[sha])])
    assert result["outcome"] == "reverted"
    assert result["commits_kept"] == 0
    assert result["reverts"][0]["author"] == "Test author"
    assert "This reverts commit" in result["reverts"][0]["reason"]


def test_late_read_excludes_changes_after_horizon(repo):
    sha = commit(repo, "new\n")
    repo[1]("revert", "--no-edit", sha, at=BASE + 250)
    events = [event("publish", commits=[sha])]
    assert read(repo, events, BASE + 200)["outcome"] == "kept"
    assert read(repo, events, BASE + 10000)["outcome"] == "kept"


def test_rebased_patch_and_unreachable_unknown(repo):
    git = repo[1]
    git("checkout", "-qb", "worker")
    sha = commit(repo, "new\n")
    git("checkout", "-q", "main")
    assert read(repo, [event("publish", commits=[sha])])["outcome"] == "unknown"
    # A separate commit with the same stable patch is evidence after rewriting.
    other = commit(repo, "new\n", at=BASE + 80)
    result = read(repo, [event("publish", commits=[sha])])
    assert result["outcome"] == "kept"
    assert result["evidence"]["matches"] == {sha: other}
    git("revert", "--no-edit", other, at=BASE + 150)
    assert read(repo, [event("publish", commits=[sha])])["outcome"] == "reverted"


def test_combined_task_squash(repo):
    git = repo[1]
    git("checkout", "-qb", "worker")
    first = commit(repo, "first\n")
    second = commit(repo, "second\n", at=BASE + 11)
    git("checkout", "-q", "main")
    squash = commit(repo, "second\n", at=BASE + 60)
    result = read(repo, [event("publish", commits=[first, second])])
    assert result["outcome"] == "kept"
    assert result["commits_kept"] == result["commits_published"] == 2
    assert set(result["evidence"]["matches"].values()) == {squash}


def test_same_vendor_approval_does_not_count_and_human_does(repo):
    sha = commit(repo, "new\n")
    pub = event("publish", commits=[sha])
    assert not read(repo, [pub, event("approval", sha=sha, actor="other", vendor="openai")])[
        "approved"
    ]
    assert read(repo, [pub, event("approval", sha=sha, actor="owner", human=True)])["approved"]
    assert not read(repo, [pub, event("approval", sha=sha, human=True, at=BASE + 201)])["approved"]


def test_git_failure_and_later_published_commit_are_unknown(repo):
    sha = commit(repo, "future\n", at=BASE + 250)
    result = read(repo, [event("publish", commits=[sha])])
    assert result["outcome"] == "unknown"
    assert (
        evaluate(
            Path("/nonexistent"), "main", task(), [event("publish", commits=[sha])], BASE + 300
        )["outcome"]
        == "unknown"
    )


def test_explicit_revert_names_pruned_original(repo):
    missing = "a" * 40
    repo[1](
        "commit", "--allow-empty", "-qm", f"Undo unsafe change\n\nReverts: {missing}", at=BASE + 150
    )
    result = read(repo, [event("publish", commits=[missing])])
    assert result["outcome"] == "reverted"
    assert result["reverts"][0]["commits"] == [missing]


def test_five_commits_one_task_and_escalation_with_rescue_work(repo):
    shas = [commit(repo, f"change {n}\n", at=BASE + n + 1) for n in range(5)]
    result = read(repo, [event("publish", commits=shas)])
    assert result["outcome"] == "kept"
    assert result["commits_published"] == result["commits_kept"] == 5
    result = read(repo, [event("publish", commits=shas), event("escalation", to="rescuer")])
    assert result["outcome"] == "escalated"
    assert result["commits_kept"] == 5


def test_usage_gaps_and_missing_attempts_never_look_free(repo):
    no_attempts = read(repo, [])
    assert no_attempts["tokens"] is None
    assert no_attempts["unknown_usage"] == 1
    assert no_attempts["attempt_count"] == 0
    assert no_attempts["duration_unknown"] == 1
    result = read(
        repo,
        [
            event("attempt", outcome="errored", tokens=4),
            event("attempt", outcome="clean", tokens=None, wall_s=0, duration_unknown=True),
        ],
    )
    assert result["attempt_count"] == 2
    assert result["duration_unknown"] == 2
    assert result["tokens"] is None and result["unknown_usage"] == 1
