"""Limit-status slice 3: usage the vendor's CLI reported (Claude only).

Spike 2026-10-21: headless `claude -p --output-format stream-json --verbose`
(2.1.304) prints a ``rate_limit_event`` whose ``unifiedWindows`` carry 5-hour and
7-day utilization (0..1) and an epoch ``resetsAt``. `codex exec --json` (0.160.1)
carries token usage only. The fixture below has that event's shape.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from quorus import limit_status

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
limits = reflexd.reflexd_limits

NOW = 1_800_000_000.0


def _event(windows: Any = None, **info: Any) -> str:
    if windows is None:
        windows = {"five_hour": {"utilization": 0.42, "resetsAt": int(NOW) + 3600},
                   "seven_day": {"utilization": 0.75, "resetsAt": int(NOW) + 4 * 86400}}
    return json.dumps({"type": "rate_limit_event", "uuid": "u", "session_id": "s",
                       "rate_limit_info": {"status": "allowed_warning",
                                           "rateLimitType": "seven_day", "utilization": 0.75,
                                           "resetsAt": int(NOW) + 4 * 86400,
                                           "isUsingOverage": False, "overageStatus": "rejected",
                                           "unifiedWindows": windows, **info}})


STREAM = "\n".join([
    '{"type":"system","subtype":"init","session_id":"s"}',
    '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    _event(),
    '{"type":"result","subtype":"success","result":"ok","session_id":"s"}',
])


def test_both_windows_are_read_as_reported_percentages() -> None:
    usage = limits.read_claude_usage(STREAM, NOW)
    assert set(usage) == {"5h", "7d"}
    five = usage["5h"]
    assert (five.kind, five.source, five.used_pct, five.resets_at, five.seen_at) == (
        "reported", "claude.stream", 42.0, NOW + 3600, NOW)
    assert usage["7d"].used_pct == 75.0


def test_the_last_event_in_the_stream_wins() -> None:
    later = _event({"five_hour": {"utilization": 0.9, "resetsAt": int(NOW) + 600}})
    usage = limits.read_claude_usage(STREAM + "\n" + later, NOW)
    assert set(usage) == {"5h"} and usage["5h"].used_pct == 90.0


@pytest.mark.parametrize("stream", [
    "", "no json here", '{"type":"result","result":"ok"}',
    '{"type":"rate_limit_event", broken',
    _event(windows="five_hour"), _event(windows=[]),
    json.dumps({"type": "rate_limit_event", "rate_limit_info": "rejected"}),
    json.dumps({"type": "assistant", "text": "the rate_limit_event said 99%"}),
])
def test_no_usable_event_means_no_usage(stream: str) -> None:
    assert limits.read_claude_usage(stream, NOW) == {}


@pytest.mark.parametrize("bad", [
    {"utilization": 1.5, "resetsAt": NOW + 60}, {"utilization": -0.1},
    {"utilization": "0.5"}, {"utilization": True}, {"utilization": float("nan")},
    {"utilization": 10 ** 400}, {"utilization": None}, {"resetsAt": NOW + 60},
    {"utilization": 0.5, "resetsAt": "soon"}, {"utilization": 0.5, "resetsAt": True},
    "0.5", None,
])
def test_a_malformed_window_is_dropped_and_the_other_kept(bad: Any) -> None:
    stream = _event({"five_hour": bad, "seven_day": {"utilization": 0.1}})
    usage = limits.read_claude_usage(stream, NOW)
    assert set(usage) == {"7d"} and usage["7d"].resets_at is None


@pytest.mark.parametrize("reset", [NOW - 1, NOW + 30 * 86400, 10 ** 400])
def test_an_untrusted_reset_keeps_the_usage_but_not_the_time(reset: float) -> None:
    usage = limits.read_claude_usage(
        _event({"five_hour": {"utilization": 0.5, "resetsAt": reset}}), NOW)
    assert usage["5h"].used_pct == 50.0 and usage["5h"].resets_at is None


def test_usage_ages_out_and_is_unknown_after_its_reset() -> None:
    usage = limits.read_claude_usage(STREAM, NOW)
    assert set(limits.live_usage(usage, NOW + 20 * 60)) == {"5h", "7d"}  # stale, still shown
    assert set(limits.live_usage(usage, NOW + 3600 + 1)) == set()  # past 1 h and the 5h reset
    week_only = {"7d": usage["7d"]}
    assert limits.live_usage(week_only, NOW + 3601) == {}  # an hour old: dropped


# ── the adapter and presence ────────────────────────────────────────────


class _Proc:
    def __init__(self, out: str, rc: int = 0) -> None:
        self.returncode, self._out = rc, out.encode()

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, b""


def _run(monkeypatch, binary: str, out: str, rc: int = 0) -> Any:
    async def fake_exec(*_a: Any, **_k: Any) -> _Proc:
        return _Proc(out, rc)

    monkeypatch.setattr(reflexd.shutil, "which", lambda b: f"/bin/{b}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    adapter = reflexd.HeadlessAdapter(timeout_s=5)
    asyncio.run(adapter._run_subprocess([binary, "-p", "x"], parser=str))
    return adapter


def test_a_claude_turn_records_its_usage(monkeypatch) -> None:
    stream = STREAM.replace(str(int(NOW)), str(int(time.time())))
    now_stream = "\n".join(
        _event({"five_hour": {"utilization": 0.42, "resetsAt": int(time.time()) + 3600}})
        if '"rate_limit_event"' in line else line for line in stream.splitlines())
    adapter = _run(monkeypatch, "claude", now_stream)
    assert adapter.current_usage()["5h"].used_pct == 42.0
    failed = _run(monkeypatch, "claude", now_stream, rc=1)  # a failed turn still reports
    assert "5h" in failed.usage


def test_other_harnesses_are_not_read(monkeypatch) -> None:
    assert _run(monkeypatch, "codex", STREAM).usage == {}


def test_presence_sends_usage_the_relay_accepts() -> None:
    cfg = reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                participant_name="a-claude", spawn_enabled=False)
    d = reflexd.Reflexd(cfg)
    now = time.time()
    d.adapter.usage = limits.read_claude_usage(
        _event({"five_hour": {"utilization": 0.42, "resetsAt": int(now) + 3600},
                "seven_day": {"utilization": 0.75}}), now)
    d.adapter.note_limit("You've hit your usage limit, retry in 2h")
    extra = d._presence_extra("idle")
    assert [r["window"] for r in extra["readings"]] == ["limit", "5h", "7d"]
    cleaned = limit_status.clean(extra["pause"], extra["readings"], time.time())
    assert [(r["window"], r["used_pct"]) for r in cleaned["readings"]] == [
        ("limit", None), ("5h", 42.0), ("7d", 75.0)]
