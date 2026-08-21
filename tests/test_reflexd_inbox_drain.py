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
