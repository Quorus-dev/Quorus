"""Silence must be visible (Arav, 2026-10-17): a user can't tell an agent that
chose not to reply, or agents paused by the loop guard, from a dead agent."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

from quorus_tui.chat import render_bubble_feed

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict] = []

    async def fetch_recent(self, *, room: str, limit: int = 10):
        return []

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": "p1"}


def test_no_reply_leaves_a_quiet_status_line(monkeypatch) -> None:
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))

    async def fake_run(harness, **_kw: Any) -> str:
        return "NO_REPLY"

    monkeypatch.setattr(d.adapter, "run", fake_run)
    relay = _Relay()
    env = {"id": "e", "message_id": "m1", "room": "build", "from_name": "a-claude",
           "content": "thanks @a-codex", "message_type": "chat"}
    asyncio.run(d._wake_and_reply(relay, env, reason="t"))
    assert relay.posted == [{"room": "build", "from_name": "a-codex",
                             "content": "@a-codex read it, nothing to add",
                             "message_type": "status", "reply_to": "m1"}]
    # A status line never wakes anyone.
    res = reflexd.classify_message(content="@a-claude read it", sender="a-codex",
                                   self_name="a-claude", message_type="status")
    assert res.action == "IGNORE"


def test_pause_is_announced_once_per_pause() -> None:
    human = {"from_name": "arav", "content": "go"}
    agent = {"from_name": "a-claude", "content": "done"}
    notice = {"from_name": "a-codex", "content": f"{reflexd.PAUSE_NOTICE} after 6"}
    assert not reflexd._pause_announced([human, agent, agent])
    assert reflexd._pause_announced([human, agent, notice, agent])
    # A new human message starts a new pause cycle.
    assert not reflexd._pause_announced([notice, human, agent])


def test_status_renders_as_a_quiet_system_line() -> None:
    msg = {"from_name": "a-codex", "content": "@a-codex read it, nothing to add",
           "message_type": "status", "timestamp": "2026-10-17T10:00:00Z"}
    rows = [r.plain for r in render_bubble_feed([msg], "build", "arav", console_width=100)]
    assert any("read it, nothing to add" in r for r in rows)
    assert not any(r.strip().startswith("╭") for r in rows)  # not a chat bubble


def test_chatter_stop_and_full_pause_are_told_apart() -> None:
    human = {"from_name": "arav", "content": "go"}
    chatter = {"from_name": "a-codex", "content": f"{reflexd.CHATTER_NOTICE} after 6"}
    assert reflexd._pause_announced([human, chatter], reflexd.CHATTER_NOTICE)
    # A chatter notice doesn't suppress the later full-pause notice.
    assert not reflexd._pause_announced([human, chatter], reflexd.PAUSE_NOTICE)


def test_wake_repairs_an_identity_the_agent_broke(tmp_path, monkeypatch) -> None:
    # Live 2026-10-17: an agent ran `quorus init myagent --relay-url
    # http://localhost:9999` in its wake; its QUORUS_CONFIG_DIR pointed at its
    # own identity, so the bogus profile became current and its tools 401'd.
    import json

    cfg = reflexd.ReflexdConfig(relay_url="http://r", api_key="sec", legacy_bearer=True,
                                participant_name="a-opencode", spawn_enabled=False)
    d = reflexd.Reflexd(cfg)
    # Sign-in found a relay without participant keys, so the agent runs on the secret.
    d._relay = type("R", (), {"keys_supported": False})()
    agent_dir = tmp_path / "agent"
    d.adapter.agent_config_dir = agent_dir
    (agent_dir / "profiles").mkdir(parents=True)
    (agent_dir / "profiles" / "default.json").write_text('{"relay_secret": "s3cret"}')
    (agent_dir / "config.json").write_text('{"current": "default"}')
    d._refresh_identity()
    assert json.loads((agent_dir / "config.json").read_text())["current"] == "a-opencode"
    assert sorted(p.name for p in (agent_dir / "profiles").iterdir()) == ["a-opencode.json"]
    prof = json.loads((agent_dir / "profiles" / "a-opencode.json").read_text())
    assert prof["relay_secret"] == "sec" and prof["relay_url"] == "http://r"


def test_full_pause_notice_covers_chatter() -> None:
    human = {"from_name": "arav", "content": "go"}
    full = {"from_name": "a-gemini", "content": f"{reflexd.PAUSE_NOTICE} after 7"}
    assert reflexd._pause_announced([human, full], reflexd.CHATTER_NOTICE)


class _HistRelay(_Relay):
    def __init__(self, history: list[dict]) -> None:
        super().__init__()
        self.history = history

    async def fetch_recent(self, *, room: str, limit: int = 10):
        return self.history


def test_queued_mention_already_seen_and_answered_is_skipped(monkeypatch) -> None:
    # Live 2026-10-17: the lead woke ~10 times in a row for queued mentions it
    # had already seen in an earlier wake's transcript and answered.
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-claude"))
    calls: list[str] = []

    async def fake_run(harness, **_kw: Any) -> str:
        calls.append("run")
        return "Reviewed both: approved."

    monkeypatch.setattr(d.adapter, "run", fake_run)
    m1 = {"id": "m1", "message_id": "m1", "room": "r", "from_name": "a-codex",
          "content": "@a-claude review x", "message_type": "chat"}
    m2 = {"id": "m2", "message_id": "m2", "room": "r", "from_name": "a-gemini",
          "content": "@a-claude review y", "message_type": "chat"}
    relay = _HistRelay([m1, m2])  # m2 is already in the room when m1's wake runs
    asyncio.run(d._wake_and_reply(relay, m1, reason="t"))
    asyncio.run(d._wake_and_reply(relay, m2, reason="t"))
    assert calls == ["run"]
    # A message that arrived after the answered wake still wakes the agent.
    m3 = dict(m2, id="m3", message_id="m3")
    asyncio.run(d._wake_and_reply(_HistRelay([m1, m2, m3]), m3, reason="t"))
    assert calls == ["run", "run"]


def test_silence_line_at_most_once_per_ten_minutes(monkeypatch) -> None:
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))

    async def fake_run(harness, **_kw: Any) -> str:
        return "NO_REPLY"

    monkeypatch.setattr(d.adapter, "run", fake_run)
    relay = _Relay()
    for i in range(3):
        env = {"id": f"e{i}", "message_id": f"m{i}", "room": "build",
               "from_name": "a-claude", "content": "thanks @a-codex", "message_type": "chat"}
        asyncio.run(d._wake_and_reply(relay, env, reason="t"))
    assert len(relay.posted) == 1
