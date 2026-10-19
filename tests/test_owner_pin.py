"""An invitee's agent trusts its owner's key, not their name (Codex's trust call
on 3212878c, docs/internal/bets/invitee-agents.md).

`quorus agent add` with an invite key pins the owner's claim. A write-capable
turn then needs the relay's stamp to show that claim on the owner's request:
a "bob" stamped with the shared secret (the relay owner), another claim (bob's
name released and claimed again), or no stamp at all means read-only."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from quorus.runtime import owner_pin

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd_pin", _ROOT / "scripts" / "reflexd.py")
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd_pin"] = reflexd
_spec.loader.exec_module(reflexd)

PIN = {"tenant": "_legacy", "claim_id": "c" * 32}
BOB_KEY = {"sender_tenant": "_legacy", "sender_auth": "key", "sender_claim_id": "c" * 32}


def _stored(mid: str, who: str, reply_to: str | None = None, **stamp) -> dict:
    return {"id": mid, "from_name": who, "content": "fix calc.py", "reply_to": reply_to,
            **stamp}


def _delivered(stored: dict) -> dict:
    """What the daemon receives: a fan-out copy with its own id."""
    return {**{k: v for k, v in stored.items() if not k.startswith("sender_")},
            "id": "delivery-" + stored["id"], "message_id": stored["id"], "room": "build"}


class _Relay:
    def __init__(self, history: list[dict]) -> None:
        self.history = history

    async def fetch_recent(self, *, room: str, limit: int = 10):
        return self.history[-limit:]


@pytest.fixture
def daemon(monkeypatch, tmp_path: Path):
    pins = tmp_path / "agent-owner-pins.json"
    monkeypatch.setattr(owner_pin, "PINS_PATH", pins)
    monkeypatch.setenv("REFLEXD_OWNER", "bob")
    bindings = tmp_path / "room-bindings.json"
    bindings.write_text(json.dumps({"build": {"path": str(tmp_path), "mode": "autonomous"}}))
    monkeypatch.setattr(reflexd, "ROOM_BINDINGS_PATH", bindings)
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://alice:8080", api_key="k",
                                              participant_name="bob-codex",
                                              runtime_dir=tmp_path / "rt"))
    d.pins = pins
    return d


def _wake(d, envelope: dict, history: list[dict]) -> bool:
    return asyncio.run(reflexd.Reflexd._trusted_wake(d, _Relay(history), envelope, []))


def test_the_owners_own_key_gets_a_full_turn(daemon) -> None:
    owner_pin.save_pin("bob-codex", **PIN, path=daemon.pins)
    msg = _stored("m1", "bob", **BOB_KEY)
    assert _wake(daemon, _delivered(msg), [msg])


@pytest.mark.parametrize("stamp", [
    {"sender_tenant": "_legacy", "sender_auth": "secret"},  # the relay owner as "bob"
    {**BOB_KEY, "sender_claim_id": "d" * 32},  # bob's name, someone else's claim
    {**BOB_KEY, "sender_tenant": "other"},
    {},  # no stamp
])
def test_anything_but_the_owners_key_is_read_only(daemon, stamp) -> None:
    owner_pin.save_pin("bob-codex", **PIN, path=daemon.pins)
    msg = _stored("m1", "bob", **stamp)
    assert not _wake(daemon, _delivered(msg), [msg])


def test_a_message_missing_from_history_is_read_only(daemon) -> None:
    owner_pin.save_pin("bob-codex", **PIN, path=daemon.pins)
    msg = _stored("m1", "bob", **BOB_KEY)
    assert not _wake(daemon, _delivered(msg), [])


def test_the_owners_agent_carries_the_owners_key_trust(daemon) -> None:
    owner_pin.save_pin("bob-codex", **PIN, path=daemon.pins)
    root = _stored("m1", "bob", **BOB_KEY)
    hop = _stored("m2", "bob-claude", reply_to="m1")
    assert _wake(daemon, _delivered(hop), [root, hop])
    forged_root = _stored("m1", "bob", sender_tenant="_legacy", sender_auth="secret")
    assert not _wake(daemon, _delivered(hop), [forged_root, hop])


def test_no_pin_keeps_todays_rules(daemon) -> None:
    msg = _stored("m1", "bob", sender_tenant="_legacy", sender_auth="secret")
    assert _wake(daemon, _delivered(msg), [msg])  # the owner's own relay, by name


@pytest.mark.parametrize("raw", ["not json", "[]", '{"bob-codex": {"tenant": "_legacy"}}'])
def test_an_unreadable_pin_file_is_read_only(daemon, raw) -> None:
    daemon.pins.write_text(raw)
    msg = _stored("m1", "bob", **BOB_KEY)
    assert not _wake(daemon, _delivered(msg), [msg])


def test_a_pin_file_without_this_agent_leaves_it_alone(daemon) -> None:
    owner_pin.save_pin("carol-codex", **PIN, path=daemon.pins)
    assert owner_pin.owner_pin("bob-codex", daemon.pins) is None
