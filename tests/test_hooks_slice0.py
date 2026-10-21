"""Chat-list slice 0 (docs/internal/bets/chat-list.md, Codex GO at a9d48d2a).

Why the BetU traffic reached the overseer: the hooks fetched with ``ack=manual`` and
never acked, so the relay re-sent every message after 60 s, and ``_filter_unseen``
compared uuid4 ids as strings (``msg_id > last_seen or msg_id != last_seen``), which let
every id through except the exact last one. Slice 0: hooks and ``quorus inbox`` ack
exactly what they showed, and de-duplicate by delivery id.

The fake relay below models the real queue contract (``quorus/backends/memory.py``):
a manual-ack fetch moves messages to pending, an un-acked message comes back after the
visibility timeout, and an ack by delivery id removes it for good.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
from quorus_cli import hooks

RELAY = "http://relay.test"
AGENT = "alice"
TIMEOUT = 60.0


class _Resp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeRelay:
    """Manual-ack queue with a visibility timeout, like the real backend."""

    def __init__(self) -> None:
        self.queue: list[dict] = []
        self.pending: dict[str, tuple[dict, float]] = {}
        self.acked: list[list[str]] = []
        self.now = 1_000.0

    def enqueue(self, *msgs: dict) -> None:
        self.queue.extend(msgs)

    def elapse(self, seconds: float) -> None:
        self.now += seconds

    def _redeliver_stale(self) -> None:
        for did, (m, at) in list(self.pending.items()):
            if self.now - at > TIMEOUT:
                self.queue.append(m)
                del self.pending[did]

    def get(self, url: str, **kw) -> _Resp:
        self._redeliver_stale()
        if url.endswith("/peek"):
            return _Resp(200, {"count": len(self.queue), "pending": len(self.pending)})
        assert kw.get("params", {}).get("ack") == "manual", "hooks must fetch ack=manual"
        msgs = [dict(m, _delivery_id=m["id"]) for m in self.queue]
        for m in msgs:
            self.pending[m["id"]] = (m, self.now)
        self.queue.clear()
        return _Resp(200, {"messages": msgs, "ack_token": json.dumps([m["id"] for m in msgs])})

    def post(self, url: str, **kw) -> _Resp:
        assert url.endswith("/ack")
        ids = list(kw["json"]["delivery_ids"])
        self.acked.append(ids)
        for did in ids:
            self.pending.pop(did, None)
        return _Resp(200, {"status": "acked", "count": len(ids)})


def _msg(i: int, room: str = "weekend-quorus") -> dict:
    return {"id": f"m{i:03d}", "room": room, "from_name": "arav",
            "content": f"message {i}", "timestamp": "2026-10-20T19:00:00Z"}


@pytest.fixture
def relay(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # the cursors file lives under ~/.quorus
    fake = FakeRelay()
    monkeypatch.setattr(hooks.httpx, "get", fake.get)
    monkeypatch.setattr(hooks.httpx, "post", fake.post)
    monkeypatch.setattr(hooks, "_resolve_auth", lambda: (RELAY, AGENT, {"Authorization": "x"}))
    return fake


def _prompt() -> str:
    """One Gemini BeforeAgent prompt; returns the context the agent would see."""
    out = io.StringIO()
    with redirect_stdout(out):
        assert hooks.handle_gemini_beforeagent() == 0
    return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


def test_a_message_is_shown_once_across_two_prompts(relay):
    relay.enqueue(_msg(1), _msg(2))
    first = _prompt()
    assert "message 1" in first and "message 2" in first
    relay.elapse(TIMEOUT + 1)  # even past the visibility timeout...
    second = _prompt()
    assert second == "", second  # ...nothing comes back, because it was acked
    assert relay.pending == {} and relay.queue == []


def test_an_unacked_message_is_not_shown_again_after_60s(relay):
    """The redelivery path itself: if an ack is lost, the relay re-sends after 60 s and the
    seen-id set still keeps it off the screen, while the hook acks it again."""
    relay.enqueue(_msg(1))
    assert "message 1" in _prompt()
    relay.pending["m001"] = (dict(_msg(1), _delivery_id="m001"), relay.now)  # ack lost
    relay.elapse(TIMEOUT + 1)
    assert _prompt() == ""
    assert relay.acked[-1] == ["m001"] and relay.pending == {}


def test_nothing_that_was_not_shown_is_acked(relay):
    relay.enqueue(*(_msg(i) for i in range(1, 26)))  # 25 > SHOW_CAP
    first = _prompt()
    shown_ids = [f"m{i:03d}" for i in range(6, 26)]
    assert all(f"message {i}" in first for i in range(6, 26))
    assert all(f"message {i} " not in first + " " for i in range(1, 6))
    assert relay.acked == [shown_ids]
    assert sorted(relay.pending) == [f"m{i:03d}" for i in range(1, 6)]
    relay.elapse(TIMEOUT + 1)  # the five unshown ones come back and are shown now
    second = _prompt()
    assert all(f"message {i}" in second for i in range(1, 6))
    assert all(f"message {i}" not in second for i in range(6, 26))
    assert relay.pending == {}


def test_dedupe_is_by_delivery_id_not_string_order():
    """The old compare let 'a' through after 'b' and 'b' after 'a' forever."""
    cursors: dict = {}
    batch = [{"id": "b", "room": "r"}, {"id": "a", "room": "r"}]
    fresh = hooks._filter_unseen(batch, cursors)
    assert [m["id"] for m in fresh] == ["b", "a"]
    hooks._mark_seen(cursors, fresh)
    assert hooks._filter_unseen(batch, cursors) == []
    assert hooks._filter_unseen([{"id": "a"}, {"id": "a"}, {"id": "c"}], cursors) == [{"id": "c"}]


def test_cursor_stop_and_claude_stop_use_the_same_bookkeeping(relay, tmp_path):
    (tmp_path / ".quorus").mkdir(exist_ok=True)
    (tmp_path / ".quorus" / f"pending-{AGENT}.flag").write_text("")
    relay.enqueue(_msg(7))
    out = io.StringIO()
    with redirect_stdout(out):
        assert hooks.handle_cursor_stop() == 0
    assert "message 7" in json.loads(out.getvalue())["followup_message"]
    assert relay.acked == [["m007"]]
    relay.enqueue(_msg(8))
    out = io.StringIO()
    with redirect_stdout(out), patch("sys.stdin", io.StringIO("{}")):
        assert hooks.handle_claude_stop() == 0
    decision = json.loads(out.getvalue())
    assert decision["decision"] == "block" and "message 8" in decision["reason"]
    assert "message 7" not in decision["reason"]
    assert relay.acked == [["m007"], ["m008"]]


def test_a_failed_ack_never_breaks_the_hook(relay, tmp_path):
    relay.enqueue(_msg(1))

    def boom(*a, **k):
        raise RuntimeError("relay hiccup")

    with patch.object(hooks.httpx, "post", boom):
        assert "message 1" in _prompt()
    log = (tmp_path / ".quorus" / "hook-debug.log").read_text()
    assert "ack-exc" in log


def test_the_seen_set_is_bounded(relay):
    relay.enqueue(*(_msg(i) for i in range(1, hooks.SEEN_CAP + 30)))
    for _ in range(40):  # drain in SHOW_CAP-sized prompts
        if relay.queue == [] and relay.pending == {}:
            break
        _prompt()
        relay.elapse(TIMEOUT + 1)
    from quorus_cli.inbox_delivery import receipt_key
    seen = hooks._load_cursors(receipt_key(RELAY, AGENT))["_seen"]
    assert len(seen) == hooks.SEEN_CAP


@pytest.mark.asyncio
async def test_backend_redelivers_only_what_was_not_acked_after_60s(monkeypatch):
    """The real in-memory backend: ack two of three by delivery id, advance 61 s, and
    only the third comes back. This is the contract the hooks now rely on."""
    from quorus.backends import memory as mem

    clock = {"t": 10_000.0}
    monkeypatch.setattr(mem.time, "time", lambda: clock["t"])
    backend = mem.InMemoryMessageBackend()
    for i in (1, 2, 3):
        await backend.enqueue("t", AGENT, {"id": f"m{i}", "content": str(i)})
    msgs, _token = await backend.fetch("t", AGENT)
    assert [m["_delivery_id"] for m in msgs] == ["m1", "m2", "m3"]
    assert await backend.ack_ids("t", AGENT, ["m1", "m2"]) == 2
    clock["t"] += 30
    assert (await backend.fetch("t", AGENT))[0] == []  # not yet due
    clock["t"] += 31
    again, _ = await backend.fetch("t", AGENT)
    assert [m["id"] for m in again] == ["m3"]
