"""Out of quota → stop waking that agent until it resets (gemini, 2026-10-17).

Gemini's free tier allows ~20 requests a day on its default model; the daemon
kept waking it and posted the same ⚠ on every message.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)

GEMINI_ERR = ("QuotaError: You have exhausted your daily quota on this model. "
              "Please retry in 21h39m46.66s.")


def test_retry_after_is_parsed_and_capped() -> None:
    assert abs(reflexd._retry_after_s(GEMINI_ERR) - (21 * 3600 + 39 * 60 + 46.66)) < 1
    assert reflexd._retry_after_s("rate limit, retry in 30s") == 60  # floor
    assert reflexd._retry_after_s("quota exceeded") == reflexd.LIMIT_DEFAULT_S
    assert reflexd._retry_after_s("retry in 99h") == reflexd.LIMIT_MAX_S
    assert reflexd._is_limit(GEMINI_ERR) and not reflexd._is_limit("segfault")


def test_daily_quota_diagnosis_names_the_fix() -> None:
    msg = reflexd.diagnose_harness_failure("gemini", GEMINI_ERR)
    assert msg and "gemini-3.5-flash-lite" in msg


class _Proc:
    returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", GEMINI_ERR.encode()


def test_quota_error_sets_the_cooldown(monkeypatch) -> None:
    async def fake_exec(*_a: Any, **_k: Any) -> _Proc:
        return _Proc()

    monkeypatch.setattr(reflexd.shutil, "which", lambda b: f"/bin/{b}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    a = reflexd.HeadlessAdapter(timeout_s=5)
    asyncio.run(a._run_subprocess(["gemini", "-p", "x"], parser=str))
    assert a.limited_until > time.time() + 21 * 3600


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict] = []

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": "p"}

    async def fetch_recent(self, **_kw):
        return []


def test_limited_agent_is_not_woken_and_says_so_once(monkeypatch) -> None:
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-gemini"))
    d.adapter.limited_until = time.time() + 3600
    woke: list[str] = []

    async def fake_run(*_a: Any, **_k: Any) -> str:
        woke.append("x")
        return "hi"

    monkeypatch.setattr(d.adapter, "run", fake_run)
    relay = _Relay()
    env = {"id": "e", "message_id": "m", "room": "build", "from_name": "arav",
           "content": "hello team", "message_type": "chat"}
    for _ in range(3):
        asyncio.run(d._wake_and_reply(relay, env, reason="t"))
    assert woke == []
    assert len(relay.posted) == 1 and "out of quota until" in relay.posted[0]["content"]
    assert relay.posted[0]["message_type"] == "status"


class _OpencodeRateLimited:
    returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        out = ('{"type":"step_start","sessionID":"s"}\n'
               '{"type":"error","error":{"name":"AI.Error","data":{"message":'
               '"Rate limit exceeded. Please try again later."}}}\n')
        return out.encode(), b""


def test_opencode_rate_limit_on_stdout_is_diagnosed_and_cools_down(monkeypatch) -> None:
    # Live 2026-10-17: two agents on opencode's free tier got "Rate limit
    # exceeded" as a stdout JSON event (exit 1, empty stderr); the room saw a
    # bare "harness errored" and the agents kept being woken.
    async def fake_exec(*_a: Any, **_k: Any) -> _OpencodeRateLimited:
        return _OpencodeRateLimited()

    monkeypatch.setattr(reflexd.shutil, "which", lambda b: f"/bin/{b}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    a = reflexd.HeadlessAdapter(timeout_s=5)
    out = asyncio.run(a._run_subprocess(["opencode", "run"], parser=str))
    assert "rate-limited" in out
    assert a.limited_until > time.time() + 60


class _ChattyFailure:
    returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b'{"type":"text","part":{"text":"we hit our quota last week"}}\n', b""


def test_reply_text_mentioning_quota_is_not_a_limit(monkeypatch) -> None:
    async def fake_exec(*_a: Any, **_k: Any) -> _ChattyFailure:
        return _ChattyFailure()

    monkeypatch.setattr(reflexd.shutil, "which", lambda b: f"/bin/{b}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    a = reflexd.HeadlessAdapter(timeout_s=5)
    asyncio.run(a._run_subprocess(["opencode", "run"], parser=str))
    assert a.limited_until == 0.0


def test_codex_usage_limit_try_again_at_is_parsed() -> None:
    from datetime import datetime

    msg = ("You've hit your usage limit. Upgrade to Pro ... or try again at 1:46 AM.")
    now = datetime(2026, 10, 17, 0, 50)
    assert abs(reflexd._retry_after_s(msg, now=now) - (56 * 60 + 60)) < 1
    assert reflexd._is_limit(msg)
    later = datetime(2026, 10, 17, 2, 0)  # 1:46 AM already passed → tomorrow
    assert reflexd._retry_after_s(msg, now=later) > 23 * 3600
    pm = reflexd._retry_after_s("try again at 3:15 PM", now=now)
    assert abs(pm - (14 * 3600 + 25 * 60 + 60)) < 1
