"""W4 uses the real daemon's turn and publication path, without vendor calls."""
import asyncio
import json

import pytest

from quorus.ledger import read_provenance
from quorus.runtime import task_capture, task_record
from tests.test_unfinished_wakes import _daemon, _git, _Relay, _repo, reflexd


@pytest.mark.parametrize("failed", [False, True])
def test_daemon_freezes_before_run_and_records_attempt_and_publication(
        monkeypatch, tmp_path, failed):
    repo = _repo(tmp_path)
    daemon = _daemon(monkeypatch, tmp_path, repo)
    key = task_record.scope("http://r", "arav", repo)
    daemon.adapter.harness_versions = {"gemini": "0.64.0"}
    daemon.adapter.model = "model-one"

    async def run(*_args, cwd=None, **_kwargs):
        claim = next(r for r in task_record.read(repo, key) if r["type"] == "claim")
        assert (claim["kind"], claim["model"], claim["version"]) == (
            "fix", "model-one", "0.64.0")
        (cwd / "CHANGELOG.md").write_text("finished\n")
        _git(cwd, "commit", "-qam", "docs: finish")
        return "[reflexd] harness timed out" if failed else "✅ finished"

    monkeypatch.setattr(daemon.adapter, "run", run)
    envelope = dict(id="task", message_id="task", room="build", from_name="arav",
                    content="@arav-gemini kind:fix repair crash", message_type="chat")
    triage = reflexd.classify_message(content=envelope["content"], sender="arav",
                                      self_name=daemon.config.participant_name)
    asyncio.run(daemon._wake_and_reply(_Relay(), envelope, triage=triage))
    rows = task_record.read(repo, key)
    assert sum(r["type"] == "claim" for r in rows) == 1
    attempt = next(r for r in rows if r["type"] == "attempt")
    assert attempt["tokens"] is None
    assert attempt["outcome"] == ("timed_out" if failed else "clean")
    published = [r for r in rows if r["type"] == "publish"]
    assert bool(published) != failed
    if not failed:
        sha = _git(repo, "rev-parse", "main")
        note = read_provenance(repo, sha)
        assert note["task"] == "task" and note["handoff"] is False
        assert published[0]["commits"] == [sha]


def test_tokens_are_only_reported_totals_not_rate_limits_or_missing_usage():
    assert task_capture.tokens(b'{"type":"result"}', "claude") is None
    assert task_capture.tokens(b'{"type":"turn.completed","usage":{}}', "codex") is None
    assert task_capture.tokens(b'{"type":"rate_limit_event"}', "claude") is None
    for vendor, typ in (("claude", "result"), ("codex", "turn.completed")):
        row = json.dumps({"type": typ, "usage": {"input_tokens": 10, "output_tokens": 3}})
        assert task_capture.tokens((row + "\n" + row).encode(), vendor) == 26
    assert task_capture.outcome("I cannot finish this task", "clean") == "could_not"
