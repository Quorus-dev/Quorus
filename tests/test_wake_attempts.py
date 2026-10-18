"""A wake that never finishes isn't replayed forever (2026-10-17).

Each daemon restart redelivered Gemini's one hung task; it re-ran 4 times
overnight. After 2 unfinished attempts the daemon drops it and says so.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict] = []

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": "p"}

    async def fetch_recent(self, **_kw):
        return []


def _daemon(tmp_path: Path) -> Any:
    return reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                                 participant_name="a-gemini",
                                                 runtime_dir=tmp_path))


ENV = {"id": "e", "message_id": "m1", "room": "build", "from_name": "a-claude",
       "content": "@a-gemini redo the research", "message_type": "chat"}


def test_hung_task_is_dropped_after_two_unfinished_attempts(tmp_path: Path, monkeypatch) -> None:
    runs: list[str] = []

    async def hangs(*_a: Any, **_k: Any) -> None:
        runs.append("run")
        raise asyncio.CancelledError  # the daemon was restarted mid-wake

    for _ in range(2):  # two daemon lifetimes, each killed mid-wake
        d = _daemon(tmp_path)
        monkeypatch.setattr(d, "_wake_and_reply_inner", hangs)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(d._wake_and_reply(_Relay(), ENV))
    d = _daemon(tmp_path)  # third restart: give up instead of hanging again
    monkeypatch.setattr(d, "_wake_and_reply_inner", hangs)
    relay = _Relay()
    asyncio.run(d._wake_and_reply(relay, ENV))
    assert runs == ["run", "run"]
    assert "couldn't finish this after 2 tries" in relay.posted[0]["content"]
    assert relay.posted[0]["message_type"] == "status"


def test_finished_wake_clears_its_attempts(tmp_path: Path, monkeypatch) -> None:
    async def ok(*_a: Any, **_k: Any) -> None:
        return None

    for _ in range(4):  # the same id finishing each time never trips the limit
        d = _daemon(tmp_path)
        monkeypatch.setattr(d, "_wake_and_reply_inner", ok)
        asyncio.run(d._wake_and_reply(_Relay(), ENV))
    assert d._load_attempts() == {}


def test_gemini_turns_are_capped(monkeypatch) -> None:
    seen: dict = {}

    async def fake(self, build, parse, **kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(reflexd.HeadlessAdapter, "_run_with_resume", fake)
    a = reflexd.HeadlessAdapter(timeout_s=3600)
    asyncio.run(a._run_gemini("ctx", cwd=None, resume=None, on_session=None, timeout_s=3600))
    assert seen["timeout_s"] == reflexd.GEMINI_TIMEOUT_S
