"""An owner can finish a cut-off continuation without re-adopting its old tree."""

import asyncio

import pytest

from quorus.runtime import handoff_runtime, handoff_sender
from quorus.runtime.handoff_hold import PublicationHold
from tests.test_handoff_delivery import Relay
from tests.test_unfinished_wakes import _git, reflexd

pytest_plugins = ["tests.test_handoff_delivery"]


@pytest.mark.parametrize(
    "refusal", [None, "claim", "late_claim", "wrong_thread", "unrelated",
                "passing", "changed_exit"])
def test_cut_off_continuation_recovers_on_explicit_owner_retry_after_restart(
        wiring, monkeypatch, refusal):
    repo, daemon, wt, cap, _, delivery = wiring
    relay = Relay()
    base = _git(repo, "rev-parse", "main")

    async def cut_off(*a, cwd, **kw):
        (cwd / "CHANGELOG.md").write_text("receiver partial work\n")
        _git(cwd, "commit", "-qam", "preserved partial continuation")
        return "[reflexd] harness timed out"
    monkeypatch.setattr(daemon.adapter, "run", cut_off)
    asyncio.run(daemon._wake_and_reply(relay, delivery))
    partial = _git(wt.path, "rev-parse", "HEAD")
    store = handoff_runtime.journal(daemon, "build", wt)
    assert store.read(cap)["state"] == "continuing"
    assert _git(repo, "rev-parse", "main") == base
    assert PublicationHold(wt.path, store, cap).owned()

    restarted = reflexd.Reflexd(daemon.config)
    alerts = []
    monkeypatch.setattr(restarted, "_alert_owner", lambda room, text, **kw: alerts.append(text))
    modes = []

    async def finish(*a, cwd, mode="default", **kw):
        modes.append(mode)
        assert _git(cwd, "rev-parse", "HEAD") == partial
        assert (cwd / "CHANGELOG.md").read_text() == "receiver partial work\n"
        if mode != "readonly":
            (cwd / "CHANGELOG.md").write_text("completed after owner retry\n")
            _git(cwd, "commit", "-qam", "finished recovered continuation")
        return "✅ complete"
    monkeypatch.setattr(restarted.adapter, "run", finish)
    retry = {"room": "build", "message_id": "owner-retry", "from_name": "arav",
             "message_type": "chat", "content": "@arav-gemini finish the interrupted continuation",
             "reply_to": "receiver-timeout-message", "thread_root_id": cap.root}
    if refusal == "claim":
        relay.holder = "someone-else"
    elif refusal == "late_claim":
        relay.change_at = relay.lookups + 3
    elif refusal == "wrong_thread":
        retry["reply_to"] = "another-task"
        retry["thread_root_id"] = "another-task"
    elif refusal == "unrelated":
        retry["from_name"] = "arav-claude"  # agents cannot authorize owner recovery
    elif refusal in {"passing", "changed_exit"}:
        from quorus.runtime import handoff_recovery
        result = ({"status": "passing", "exit": 0} if refusal == "passing"
                  else {"status": "ran", "exit": 2})
        monkeypatch.setattr(handoff_recovery, "run_checkpoint", lambda *a, **kw: result)
    asyncio.run(restarted._wake_and_reply(relay, retry))
    if refusal is None:
        assert modes == ["autonomous"]
        assert _git(repo, "rev-list", "--count", f"{base}..main") == "3"
        assert store.read(cap)["state"] == "done"
        assert store.read(cap)["retry_head"] == partial
        assert store.read(cap)["retry_wakes"] == ["owner-retry"]
        assert not PublicationHold(wt.path, store, cap).path.exists()
        assert (repo / "CHANGELOG.md").read_text() == "completed after owner retry\n"
    else:
        assert modes == ["readonly"]
        assert _git(repo, "rev-parse", "main") == base
        assert _git(wt.path, "rev-parse", "HEAD") == partial
        assert PublicationHold(wt.path, store, cap).owned()
        if refusal in {"passing", "changed_exit"}:
            expected = ("check now passes; publish or review?" if refusal == "passing"
                        else "check failure changed; review before continuing")
            assert any(expected in text for text in alerts)
            assert any(expected in post["content"] for post in relay.posted
                       if post.get("message_type") == "status")


def test_crashed_owner_retry_is_not_relaunched_by_duplicate_delivery(wiring, monkeypatch):
    _, daemon, wt, cap, _, delivery = wiring
    relay = Relay()

    async def fail(*a, **kw):
        return "[reflexd] harness timed out"
    monkeypatch.setattr(daemon.adapter, "run", fail)
    asyncio.run(daemon._wake_and_reply(relay, delivery))
    retry = {"room": "build", "message_id": "owner-retry", "from_name": "arav",
             "message_type": "chat", "content": "retry the interrupted task", "reply_to": cap.root}
    asyncio.run(daemon._wake_and_reply(relay, retry))
    restarted = reflexd.Reflexd(daemon.config)

    async def never(*a, **kw):
        pytest.fail("a duplicate owner retry cannot launch again after restart")
    monkeypatch.setattr(restarted.adapter, "run", never)
    asyncio.run(restarted._wake_and_reply(relay, retry))
    store = handoff_runtime.journal(restarted, "build", wt)
    assert store.read(cap)["state"] == "continuing"
    assert store.read(cap)["retry_wakes"] == ["owner-retry"]
    assert PublicationHold(wt.path, store, cap).owned()


def test_recovery_has_no_environment_opt_in(monkeypatch):
    monkeypatch.setenv("REFLEXD_HANDOFFS", "1")
    assert not hasattr(handoff_sender, "ENABLED")  # no global switch at all
    assert handoff_sender.enabled("build", None) is False


def test_completed_retry_crash_releases_only_its_finished_hold_without_another_turn(
        wiring, monkeypatch):
    repo, daemon, wt, cap, _, delivery = wiring
    relay = Relay()

    async def cut_off(*a, **kw):
        return "[reflexd] harness timed out"
    monkeypatch.setattr(daemon.adapter, "run", cut_off)
    asyncio.run(daemon._wake_and_reply(relay, delivery))
    retry = {"room": "build", "message_id": "owner-retry", "from_name": "arav",
             "message_type": "chat", "content": "@arav-gemini finish", "reply_to": cap.root}

    async def finish(*a, cwd, **kw):
        (cwd / "CHANGELOG.md").write_text("recovered fix\n")
        _git(cwd, "commit", "-qam", "finish after owner retry")
        return "✅ finished"
    monkeypatch.setattr(daemon.adapter, "run", finish)

    def crash(self):
        raise OSError("crash before hold release")
    with monkeypatch.context() as patch:
        patch.setattr(PublicationHold, "release", crash)
        asyncio.run(daemon._wake_and_reply(relay, retry))
    store = handoff_runtime.journal(daemon, "build", wt)
    assert store.read(cap)["state"] == "done"
    assert PublicationHold(wt.path, store, cap).owned()
    restarted = reflexd.Reflexd(daemon.config)

    async def never(*a, **kw):
        pytest.fail("finished owner retry must not execute another harness")
    monkeypatch.setattr(restarted.adapter, "run", never)
    asyncio.run(restarted._wake_and_reply(relay, retry))
    assert not PublicationHold(wt.path, store, cap).path.exists()
    asyncio.run(restarted._publish_sweep(relay))
    assert (repo / "CHANGELOG.md").read_text() == "recovered fix\n"
