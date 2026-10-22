"""W4 task storage and capture preserve assignments across follow-up turns."""

import errno
import json
import os
import subprocess

import pytest

from quorus.runtime import task_capture as capture
from quorus.runtime import task_record as records


@pytest.fixture
def store(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    key = records.scope("https://relay.invalid/", "owner", tmp_path)
    return tmp_path, key


def claim(store, **fields):
    return records.claim(
        *store,
        "ask",
        agent="owner-codex",
        vendor="codex",
        model="model",
        version="1",
        text="kind:fix Repair it",
        now=100,
        **fields,
    )


def begin(store, envelope, *, agent="owner-codex", history=None, now=100, writable=True):
    return capture.begin(
        *store,
        envelope,
        history or [],
        agent=agent,
        vendor="codex",
        model="model",
        version="1",
        writable=writable,
        now=now,
    )


def test_kind_ambiguity_and_explicit_override():
    assert records.kind("repair the crash") == "fix"
    assert records.kind("tests/test_capture.py") == "test"
    assert records.kind("update README.md") == "docs"
    assert records.kind("do something useful") == "unknown"
    assert records.kind("fix the tests") == "unknown"
    assert records.kind("kind:feature fix the tests") == "feature"
    assert records.kind("kind:feature kind:docs") == "unknown"
    assert records.kind("kind:feature fix the crash", allow_override=False) == "fix"
    assert records.kind("kind:fix", allow_override=False) == "unknown"


@pytest.mark.parametrize("sender, expected", [
    ("owner", "docs"), ("owner-claude", "fix"), ("member", "fix"),
])
def test_only_owner_can_override_kind_even_when_other_sender_is_trusted(store, sender, expected):
    turn = begin(store, {"id": "new", "from_name": sender,
                        "content": "kind:docs repair crash"})
    row = next(r for r in records.read(*store) if r["type"] == "claim")
    assert row["kind"] == expected and turn["kind"] == expected
    later = begin(store, {"id": "followup", "reply_to": "new", "from_name": "owner",
                         "content": "kind:feature implement something"}, now=110)
    assert later["task"] == "new" and later["kind"] == expected


def test_read_only_reader_does_not_create_log(store):
    file = records.path(*store)
    assert not file.parent.exists()
    assert records.read(*store) == []
    assert records.report(store[0], scope=store[1], now=100) == []
    assert not file.parent.exists()


def test_claim_is_frozen_before_followups_and_model_changes(store):
    first = claim(store)
    again = records.claim(
        *store,
        "ask",
        agent="rescuer",
        vendor="claude",
        model="other",
        version="2",
        text="kind:docs",
        now=1000,
    )
    assert again == first
    assert first["kind"] == "fix"
    assert first["deadline"] == 100 + records.WORK_S
    assert first["read_at"] == 100 + records.READ_S
    assert len(records.read(*store)) == 1


def test_followup_ancestor_and_rescuer_share_task_not_new_claim(store):
    first = begin(store, {"id": "ask", "content": "kind:fix crash"})
    assert first["new"]
    follow = begin(
        store,
        {"id": "reply", "reply_to": "intermediate", "content": "kind:docs"},
        history=[{"id": "intermediate", "reply_to": "ask"}],
        agent="owner-claude",
        now=110,
    )
    assert follow["task"] == "ask" and not follow["new"] and follow["handoff"]
    capture.finish(*store, first, agent="owner-codex", outcome="errored", tokens=10, now=108)
    capture.finish(*store, follow, agent="owner-claude", outcome="clean", tokens=20, now=115)
    rows = records.read(*store)
    assert len([r for r in rows if r["type"] == "claim"]) == 1
    result = records.report(store[0], scope=store[1], now=100 + records.READ_S)[0]
    assert result["agent"] == "owner-codex" and result["kind"] == "fix"
    assert result["wall_s"] == 13 and result["tokens"] == 30


def test_scopes_separate_owner_relay_and_repo(store, tmp_path):
    repo, key = store
    assert records.scope("https://relay.invalid", "owner", repo) == key
    claim(store)
    for relay, owner, path in [
        ("https://other.invalid", "owner", repo),
        ("https://relay.invalid", "other", repo),
        ("https://relay.invalid", "owner", repo / "child"),
    ]:
        assert records.scope(relay, owner, path) != key
    other = records.scope("https://relay.invalid", "other", repo)
    assert records.read(repo, other) == []


def test_settlement_is_written_once_and_later_attempts_do_not_rewrite_it(store):
    row = claim(store)
    records.settle(*store, "main", now=row["read_at"] - 1)
    assert all(r["type"] != "settled" for r in records.read(*store))
    records.settle(*store, "main", now=row["read_at"])
    original = records.report(store[0], scope=store[1], now=row["read_at"])[0]
    assert original["outcome"] == "abandoned"
    # Even a newly discovered event timestamped inside the window cannot
    # rewrite a recorded read. The collector must capture evidence before it.
    records.append(
        *store,
        {
            "type": "attempt",
            "task": "ask",
            "at": 120,
            "outcome": "errored",
            "wall_s": 3,
            "tokens": 4,
        },
    )
    records.settle(*store, "main", now=row["read_at"] + 100)
    assert records.report(store[0], scope=store[1], now=row["read_at"] + 100)[0] == original
    assert len([r for r in records.read(*store) if r["type"] == "settled"]) == 1


def test_append_partial_failure_does_not_poison_next_record(store, monkeypatch):
    claim(store)
    real_write = os.write
    calls = 0

    def short_then_fail(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:4])
        raise OSError(errno.ENOSPC, "full")

    with monkeypatch.context() as patch:
        patch.setattr(records.os, "write", short_then_fail)
        with pytest.raises(OSError):
            records.append(*store, {"type": "attempt", "task": "ask"})
    records.append(*store, {"type": "attempt", "task": "ask", "at": 120})
    assert [r["type"] for r in records.read(*store)] == ["claim", "attempt"]
    for line in records.path(*store).read_text().splitlines():
        assert isinstance(json.loads(line), dict)


def test_corrupt_prior_log_refuses_claim_or_append(store):
    file = records.path(*store)
    file.parent.mkdir(parents=True)
    file.write_text('{"partial":')
    with pytest.raises(ValueError):
        claim(store)
    with pytest.raises(ValueError):
        records.append(*store, {"type": "attempt"})
    assert file.read_text() == '{"partial":'


def test_crashed_attempt_is_failed_with_unknown_usage(store):
    begin(store, {"id": "ask", "content": "kind:fix"})
    result = records.report(store[0], scope=store[1], now=100 + records.READ_S)[0]
    assert result["outcome"] == "failed"
    assert result["tokens"] is None and result["unknown_usage"] == 1


def test_tokens_missing_usage_is_unknown():
    assert capture.tokens(b'{"type":"result"}\n', "claude") is None
    assert capture.tokens(b'{"type":"turn.completed","usage":{}}\n', "codex") is None
    assert (
        capture.tokens(b'{"type":"result","usage":{"input_tokens":0,"output_tokens":0}}', "claude")
        == 0
    )
    assert (
        capture.tokens(
            b'{"type":"turn.completed","usage":{"input_tokens":7,"output_tokens":3}}', "codex"
        )
        == 10
    )


def test_status_does_not_create_claim_but_readonly_task_does(store):
    assert begin(store, {"id": "status", "message_type": "status", "content": "kind:fix"}) is None
    turn = begin(store, {"id": "review", "content": "kind:test Review the patch"}, writable=False)
    assert turn is not None and turn["task"] == "review"
