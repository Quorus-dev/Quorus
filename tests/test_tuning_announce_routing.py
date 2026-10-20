"""Self-tuning lines never reach a room (0.6.3 multi-person audit, BUG 18).

Three daemons posted "(quorus) t1 (shadow, not applied): would set @open bids …" into
a room with other owners' people in it. The note is the owner's business: it goes to
the owner as a DM (the hub shows it) and `quorus tune list` always has it. Codex, review
of af4fad72: a member list cannot prove a room is private, since a human may be called
"arav-friend", so there is no room post even when every member looks like the owner's.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reflexd


class _Relay:
    def __init__(self, members: list[str]) -> None:
        self.members, self.posted, self.dms = members, [], []

    async def fetch_members(self, *, room: str) -> list[str]:
        return list(self.members)

    async def post_reply(self, **kw: Any) -> dict[str, Any]:
        self.posted.append(kw)
        return {"id": "p1"}

    async def post_agent_dm(self, **kw: Any) -> dict[str, Any]:
        self.dms.append(kw)
        return {"id": "d1"}


def _announce(tmp_path: Path, members: list[str], monkeypatch) -> _Relay:
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    cfg = reflexd.ReflexdConfig(relay_url="http://test", api_key="k",
                                participant_name="arav-claude", runtime_dir=tmp_path,
                                log_path=tmp_path / "l.log")
    d = reflexd.Reflexd(cfg)
    relay = _Relay(members)
    text = "(quorus) arav.t1 (shadow, not applied): would set @open bids x1.30 for @arav-claude"
    asyncio.run(d._announce_adaptation(relay, "build", {"id": "m1"}, text))
    return relay


def test_a_shared_room_gets_no_tuning_line_and_the_owner_gets_a_dm(tmp_path, monkeypatch):
    relay = _announce(tmp_path, ["arav", "arav-claude", "aarya", "aarya-opencode"], monkeypatch)
    assert relay.posted == []
    [dm] = relay.dms
    assert dm["recipient"] == "arav" and dm["sender"] == "arav-claude"
    assert dm["content"].startswith("[#build] (quorus) arav.t1 (shadow, not applied)")


def test_a_human_whose_name_starts_with_the_owners_is_still_someone_else(tmp_path, monkeypatch):
    """Codex's case: members arav, arav-claude, arav-friend. No room post."""
    relay = _announce(tmp_path, ["arav", "arav-claude", "arav-friend"], monkeypatch)
    assert relay.posted == [] and len(relay.dms) == 1


def test_even_a_room_of_the_owners_own_agents_gets_no_room_post(tmp_path, monkeypatch):
    relay = _announce(tmp_path, ["arav", "arav-claude", "arav-codex"], monkeypatch)
    assert relay.posted == [] and len(relay.dms) == 1


def test_a_failed_dm_is_swallowed(tmp_path, monkeypatch):
    class _Broken(_Relay):
        async def post_agent_dm(self, **kw: Any) -> dict[str, Any]:
            raise RuntimeError("403")

    monkeypatch.setattr(reflexd, "RelayClient", _Broken, raising=False)
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    cfg = reflexd.ReflexdConfig(relay_url="http://test", api_key="k",
                                participant_name="arav-claude", runtime_dir=tmp_path,
                                log_path=tmp_path / "l.log")
    d = reflexd.Reflexd(cfg)
    relay = _Broken([])
    asyncio.run(d._announce_adaptation(relay, "build", {}, "(quorus) arav.t1 …"))
    assert relay.posted == []
