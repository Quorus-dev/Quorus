"""An @open winner that may only answer read-only stalled the task (audit 0.6.3,
bug 20): in a room that trusts only its owner, a teammate's @open went to an
agent that then couldn't write for them. Such an agent now bids the floor, so
one that may write for that person wins; alone, it still answers."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "reflexd_open_ro", Path(__file__).resolve().parent.parent / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd_open_ro"] = reflexd
_spec.loader.exec_module(reflexd)


@pytest.fixture
def daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    monkeypatch.setenv("QUORUS_TUNING_FILE", str(tmp_path / "tuning.json"))
    monkeypatch.delenv("QUORUS_TRACK_BIDS", raising=False)
    monkeypatch.setattr(reflexd, "BID_WINDOW_SECONDS", 0.2)
    monkeypatch.setattr(reflexd._owner_pin, "PINS_PATH", tmp_path / "pins.json")

    def bind(entry: dict[str, Any]) -> None:
        path = tmp_path / "room-bindings.json"
        path.write_text(json.dumps({"dev": {"path": str(tmp_path), **entry}}))
        monkeypatch.setattr(reflexd, "ROOM_BINDINGS_PATH", path)

    cfg = reflexd.ReflexdConfig(relay_url="http://relay.test", participant_name="arav-codex",
                                api_key="k", runtime_dir=tmp_path / "rt")
    d = reflexd.Reflexd(cfg)
    d.bind = bind
    return d


def _bid(daemon, sender: str) -> dict[str, Any]:
    bids: list[dict[str, Any]] = []
    msg = {"id": "m1", "room": "dev", "from_name": sender,
           "content": "@open please fix the tests", "message_type": "chat"}

    class Relay:
        async def submit_bid(self, **kw):
            bids.append(kw)
            return {}

        async def claim(self, *, room_id, message_id):
            return {"claimed": True, "winner": "aarya-codex", "bid": 0.5,
                    "candidates": ["arav-codex", "aarya-codex"]}

        async def post_social_defer(self, **kw):
            return {}

        async def post_reply(self, **kw):
            return {}

        async def fetch_members(self, *, room):
            return ["arav", "arav-codex", "sam", "aarya-codex"]

        async def fetch_recent(self, *, room, limit=10):
            return [msg]

        async def fetch_member_roles(self, *, room):
            return {"arav": "member", "arav-codex": "member", "sam": "member",
                    "aarya-codex": "member"}

    envelope = {**msg, "id": "delivery-m1", "message_id": "m1"}
    asyncio.run(daemon.handle_room_message(Relay(), envelope))
    assert bids, "an @open still bids"
    return bids[0]


@pytest.mark.parametrize("mode", ["autonomous", "default"])
def test_a_teammates_open_task_bids_the_floor_where_it_would_be_read_only(daemon, mode) -> None:
    """Arav's run: aarya's agent won arav's @open and woke read-only. Untrusted
    requests are read-only in every mode, `default` included."""
    daemon.bind({"mode": mode})
    bid = _bid(daemon, "sam")
    assert bid["bid"] <= 0.05 and bid["reason"].endswith("+read-only"), bid


def test_the_owners_open_task_bids_as_before(daemon) -> None:
    daemon.bind({"mode": "autonomous"})
    bid = _bid(daemon, "arav")
    assert bid["bid"] > 0.05 and "read-only" not in bid["reason"], bid


def test_a_members_trust_room_bids_as_before_for_anyone(daemon) -> None:
    daemon.bind({"mode": "autonomous", "trust": "members"})
    bid = _bid(daemon, "sam")
    assert "read-only" not in bid["reason"], bid
