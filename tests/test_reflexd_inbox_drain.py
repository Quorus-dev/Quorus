"""Tests for reflexd R1 (durable inbox drain) + R2 (presence heartbeat) and
the canonical bid-id fix — see docs/WAKE_REBUILD_SPEC.md Stream R/D.

R1: on every (re)connect the daemon drains ``GET /messages/{p}?ack=manual``
through the same dispatch path as live SSE events, then acks — so mentions
that arrived while the daemon was down (laptop asleep) are never lost.

Bid-id fix: bidding must key on the canonical room-message ``message_id``
(shared across recipients), never the per-recipient fan-out ``id`` — the
latter gives every agent a private auction window, so every capable agent
"wins" an ``@open`` broadcast and all of them reply.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFLEXD_PATH = _REPO_ROOT / "scripts" / "reflexd.py"
_spec = importlib.util.spec_from_file_location("reflexd_r1", _REFLEXD_PATH)
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd_r1"] = reflexd
_spec.loader.exec_module(reflexd)


SELF = "arav-claude"


def _make_daemon() -> Any:
    cfg = reflexd.ReflexdConfig(
        relay_url="http://relay.test",
        participant_name=SELF,
        api_key="k",
    )
    return reflexd.Reflexd(cfg)


class FakeRelay:
    """Stands in for RelayClient in drain/heartbeat unit tests."""

    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self.batches = list(batches)
        self.acks: list[str] = []
        self.heartbeats: list[dict[str, Any]] = []
        self.fetch_calls = 0

    async def fetch_inbox(self, *, participant: str):
        self.fetch_calls += 1
        if self.batches:
            batch = self.batches.pop(0)
            return batch, f"tok-{self.fetch_calls}"
        return [], None

    async def ack_inbox(self, *, participant: str, ack_token: str) -> None:
        self.acks.append(ack_token)

    async def post_heartbeat(
        self, *, participant: str, status: str = "active", room: str = ""
    ) -> None:
        self.heartbeats.append({"participant": participant, "status": status})


def test_drain_inbox_dispatches_and_acks(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = _make_daemon()
    seen: list[dict[str, Any]] = []

    async def fake_dispatch(relay, event_name, data):
        assert event_name == "message"
        seen.append(data)

    monkeypatch.setattr(daemon, "_dispatch_event", fake_dispatch)
    relay = FakeRelay([
        [{"id": "f1", "message_id": "m1", "room": "r", "content": "@arav-claude hi"}],
        [{"id": "f2", "message_id": "m2", "room": "r", "content": "again"}],
    ])
    asyncio.run(daemon._drain_inbox(relay))

    assert [m["message_id"] for m in seen] == ["m1", "m2"]
    # Each non-empty batch acked; loop stops on the empty fetch.
    assert relay.acks == ["tok-1", "tok-2"]
    assert relay.fetch_calls == 3


def test_drain_inbox_empty_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = _make_daemon()

    async def fail_dispatch(relay, event_name, data):  # pragma: no cover
        raise AssertionError("dispatch must not run for an empty inbox")

    monkeypatch.setattr(daemon, "_dispatch_event", fail_dispatch)
    relay = FakeRelay([])
    asyncio.run(daemon._drain_inbox(relay))
    assert relay.acks == []


def test_drain_inbox_fetch_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = _make_daemon()

    class BrokenRelay:
        async def fetch_inbox(self, *, participant: str):
            raise RuntimeError("relay down")

    # Must not raise — SSE loop still provides live delivery.
    asyncio.run(daemon._drain_inbox(BrokenRelay()))


def test_drain_inbox_handler_error_still_acks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A poison message must not wedge the queue: batch is still acked."""
    daemon = _make_daemon()

    async def exploding_dispatch(relay, event_name, data):
        raise ValueError("boom")

    monkeypatch.setattr(daemon, "_dispatch_event", exploding_dispatch)
    relay = FakeRelay([[{"id": "f1", "message_id": "m1"}]])
    asyncio.run(daemon._drain_inbox(relay))
    assert relay.acks == ["tok-1"]


def test_heartbeat_loop_posts_until_stopped() -> None:
    daemon = _make_daemon()
    relay = FakeRelay([])

    async def run() -> None:
        task = asyncio.create_task(daemon._heartbeat_loop(relay, interval=0.01))
        while len(relay.heartbeats) < 3:
            await asyncio.sleep(0.005)
        daemon._stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert len(relay.heartbeats) >= 3
    assert relay.heartbeats[0] == {"participant": SELF, "status": "active"}


def test_bid_keys_on_canonical_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bid must use the shared ``message_id``, not the fan-out ``id``."""
    daemon = _make_daemon()
    bids: list[dict[str, Any]] = []

    class BidRelay:
        async def submit_bid(self, *, room_id, message_id, **kw):
            bids.append({"room_id": room_id, "message_id": message_id})
            # Lose the auction so the handler returns without spawning.
            return {"winner": "someone-else"}

        async def claim(self, *, room_id, message_id):  # pragma: no cover
            return {}

        async def post_social_defer(self, **kw):  # pragma: no cover
            return {}

    envelope = {
        "id": "fanout-uuid-private-to-me",
        "message_id": "canonical-room-msg-id",
        "room": "dev",
        "from_name": "arav",
        "content": f"@{SELF} please fix the tests",
        "message_type": "chat",
    }
    asyncio.run(daemon.handle_room_message(BidRelay(), envelope))
    assert bids, "an @-mention must produce a bid"
    assert bids[0]["message_id"] == "canonical-room-msg-id"


def test_bid_falls_back_to_id_for_legacy_envelopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = _make_daemon()
    bids: list[str] = []

    class BidRelay:
        async def submit_bid(self, *, room_id, message_id, **kw):
            bids.append(message_id)
            return {"winner": "someone-else"}

        async def claim(self, *, room_id, message_id):  # pragma: no cover
            return {}

        async def post_social_defer(self, **kw):  # pragma: no cover
            return {}

    envelope = {
        "id": "legacy-only-id",
        "room": "dev",
        "from_name": "arav",
        "content": f"@{SELF} legacy envelope",
        "message_type": "chat",
    }
    asyncio.run(daemon.handle_room_message(BidRelay(), envelope))
    assert bids == ["legacy-only-id"]


def test_open_broadcast_only_matches_at_message_start() -> None:
    """Echo-storm regression (live 2026-08-20): a reply QUOTING "@open …"
    mid-text must not be triaged as a fresh broadcast."""
    triage = reflexd.reflexd_triage
    hit = triage.classify_message(
        content="@open fix the failing tests in the tui module",
        sender="arav", self_name=SELF, message_type="chat",
    )
    assert hit.action == "RESPOND" and hit.kind == "open_todo"
    echo = triage.classify_message(
        content="(reflexd-stub) on it, working on '@open fix the failing tests'",
        sender="aarya-claude", self_name=SELF, message_type="chat",
    )
    assert echo.kind != "open_todo"


def test_stub_reply_neutralizes_trigger_tokens() -> None:
    ctx = "@arav: TODO @backend: audit the relay and also @open ship tests"
    out = reflexd.HeadlessAdapter._stub_reply(ctx)
    assert "@open" not in out.lower().replace("@ open", "")
    assert "todo @backend" not in out.lower()
    assert "@ open" in out.lower() or "@ backend" in out.lower()


class _D7Relay:
    """Relay double for wake-success suppression tests (spec D7)."""

    def __init__(self, history: list[dict[str, Any]]) -> None:
        self.history = history
        self.posted: list[dict[str, Any]] = []

    async def fetch_recent(self, *, room: str, limit: int = 10):
        return self.history

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": "posted-1"}


def _wake_envelope() -> dict[str, Any]:
    return {
        "id": "fanout-1", "message_id": "wake-msg-1", "room": "r",
        "from_name": "arav", "content": f"@{SELF} do the thing",
        "message_type": "chat",
    }


def _run_wake(daemon, relay, monkeypatch, adapter_reply: str) -> None:
    async def fake_run(harness, *, context, cwd=None, resume=None, on_session=None):
        return adapter_reply
    monkeypatch.setattr(daemon.adapter, "run", fake_run)
    asyncio.run(daemon._wake_and_reply(relay, _wake_envelope(), reason="test"))


def test_d7_timeout_suppressed_when_agent_already_replied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent posted its own reply via quorus tools → don't post 'timed out'."""
    daemon = _make_daemon()
    relay = _D7Relay(history=[
        {"id": "m9", "from_name": SELF, "reply_to": "wake-msg-1",
         "content": "done, PR opened"},
    ])
    _run_wake(daemon, relay, monkeypatch, "[reflexd] harness timed out")
    assert relay.posted == []


def test_d7_timeout_still_posted_when_no_self_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = _make_daemon()
    relay = _D7Relay(history=[
        {"id": "m9", "from_name": "someone-else", "reply_to": "wake-msg-1",
         "content": "unrelated"},
    ])
    _run_wake(daemon, relay, monkeypatch, "[reflexd] harness timed out")
    assert len(relay.posted) == 1
    assert relay.posted[0]["content"] == "[reflexd] harness timed out"


def test_d7_real_replies_never_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = _make_daemon()
    relay = _D7Relay(history=[
        {"id": "m9", "from_name": SELF, "reply_to": "wake-msg-1",
         "content": "earlier reply"},
    ])
    _run_wake(daemon, relay, monkeypatch, "the actual model answer")
    assert len(relay.posted) == 1
    assert relay.posted[0]["content"] == "the actual model answer"


def test_workspace_for_resolution(tmp_path: Path) -> None:
    """D1: binding resolves only to existing dirs; garbage degrades to None."""
    bindings = tmp_path / "room-bindings.json"
    ws = tmp_path / "repo"
    ws.mkdir()
    bindings.write_text(
        '{"dev": "%s", "gone": "%s/nope", "junk": 42}' % (ws, tmp_path)
    )
    assert reflexd.workspace_for("dev", bindings_path=bindings) == ws
    assert reflexd.workspace_for("gone", bindings_path=bindings) is None
    assert reflexd.workspace_for("junk", bindings_path=bindings) is None
    assert reflexd.workspace_for("absent", bindings_path=bindings) is None
    assert reflexd.workspace_for("dev", bindings_path=tmp_path / "missing.json") is None
    bindings.write_text("not json at all")
    assert reflexd.workspace_for("dev", bindings_path=bindings) is None


def test_subprocess_runs_in_bound_workspace(tmp_path: Path) -> None:
    """D1: the harness subprocess actually executes inside the bound dir."""
    adapter = reflexd.HeadlessAdapter(timeout_s=10)
    out = asyncio.run(adapter._run_subprocess(
        ["pwd"], parser=lambda o: o.strip(), cwd=tmp_path,
    ))
    assert Path(out).resolve() == tmp_path.resolve()


def test_unbound_room_gets_prompt_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """D1: with no binding, the wake prompt tells the model how to bind."""
    daemon = _make_daemon()
    captured: dict[str, Any] = {}

    async def fake_run(harness, *, context, cwd=None, resume=None, on_session=None):
        captured["context"] = context
        captured["cwd"] = cwd
        return "ok"

    monkeypatch.setattr(daemon.adapter, "run", fake_run)
    monkeypatch.setattr(reflexd, "workspace_for", lambda room, **kw: None)
    relay = _D7Relay(history=[])
    asyncio.run(daemon._wake_and_reply(relay, _wake_envelope(), reason="test"))
    assert "No workspace is bound" in captured["context"]
    assert captured["cwd"] is None


def test_session_map_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D2: remember → resolve → forget, file mode 0600."""
    monkeypatch.setattr(reflexd, "_sessions_path",
                        lambda p: tmp_path / f"sessions-{p}.json")
    assert reflexd.session_for(SELF, "dev") is None
    reflexd.remember_session(SELF, "dev", "sid-abc")
    assert reflexd.session_for(SELF, "dev") == "sid-abc"
    mode = (tmp_path / f"sessions-{SELF}.json").stat().st_mode & 0o777
    assert mode == 0o600
    reflexd.forget_session(SELF, "dev")
    assert reflexd.session_for(SELF, "dev") is None


def test_parse_claude_json_envelope_and_fallback() -> None:
    text, sid = reflexd._parse_claude_json(
        '{"result": "the answer", "session_id": "s-1", "total_cost_usd": 0.01}'
    )
    assert (text, sid) == ("the answer", "s-1")
    text, sid = reflexd._parse_claude_json("plain old text output\n")
    assert (text, sid) == ("plain old text output", None)


def test_wake_resumes_and_persists_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2 e2e at the wake layer: prior session is passed as resume; the
    session id returned by the harness is persisted for the next wake."""
    monkeypatch.setattr(reflexd, "_sessions_path",
                        lambda p: tmp_path / f"sessions-{p}.json")
    reflexd.remember_session(SELF, "r", "old-session")
    daemon = _make_daemon()
    seen: dict[str, Any] = {}

    async def fake_run(harness, *, context, cwd=None, resume=None, on_session=None):
        seen["resume"] = resume
        if on_session:
            on_session("new-session-id")
        return "continued fine"

    monkeypatch.setattr(daemon.adapter, "run", fake_run)
    relay = _D7Relay(history=[])
    asyncio.run(daemon._wake_and_reply(relay, _wake_envelope(), reason="test"))
    assert seen["resume"] == "old-session"
    assert reflexd.session_for(SELF, "r") == "new-session-id"


def test_claude_resume_failure_retries_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: an expired resume id degrades to a fresh session, never a dead wake."""
    adapter = reflexd.HeadlessAdapter(timeout_s=2)
    calls: list[list[str]] = []

    async def fake_sub(argv, *, parser, cwd=None):
        calls.append(argv)
        if "--resume" in argv:
            return "[reflexd] harness errored"
        return parser('{"result": "fresh reply", "session_id": "s-new"}')

    monkeypatch.setattr(adapter, "_run_subprocess", fake_sub)
    got: list[str] = []
    out = asyncio.run(adapter.run(
        "claude", context="hi", resume="dead-session",
        on_session=got.append,
    ))
    assert out == "fresh reply"
    assert len(calls) == 2 and "--resume" in calls[0] and "--resume" not in calls[1]
    assert got == ["s-new"]
