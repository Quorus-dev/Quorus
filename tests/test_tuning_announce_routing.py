"""Self-tuning lines stay out of shared rooms (0.6.3 multi-person audit, BUG 18).

Three daemons posted "(quorus) t1 (shadow, not applied): would set @open bids …" into
a room with other owners' people in it. The note is the owner's business: in a shared
room it goes to the owner as a DM; in a room of the owner's own agents it stays a
status line, which is where the board reads it.
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


def test_a_room_of_the_owners_own_agents_keeps_the_status_line(tmp_path, monkeypatch):
    relay = _announce(tmp_path, ["arav", "arav-claude", "arav-codex"], monkeypatch)
    assert relay.dms == []
    [post] = relay.posted
    assert post["message_type"] == "status" and post["room"] == "build"


def test_unknown_membership_counts_as_shared(tmp_path, monkeypatch):
    relay = _announce(tmp_path, [], monkeypatch)
    assert relay.posted == [] and len(relay.dms) == 1
