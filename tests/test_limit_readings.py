"""Limit-status slice 1: the vendor's reading is kept apart from Quorus's pause.

Design: docs/internal/bets/limit-status.md rev 3 (Codex plan GO on ab26832d +
b53d4c98). The pause (``limited_until``) keeps 0.6.5's padding and cap and drives
scheduling; the reading carries the vendor's exact reset and a source id, and
drives what the room is told. Cooldowns we impose on free models are never
"out of quota".
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
limits = reflexd.reflexd_limits

T = 1_800_000_000.0  # a fixed "now" for the clock-advance cases


def _daemon() -> Any:
    cfg = reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                participant_name="a-gemini", spawn_enabled=False)
    return reflexd.Reflexd(cfg)


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict] = []

    async def post_reply(self, **kw: Any) -> dict:
        self.posted.append(kw)
        return {"id": "p"}

    async def fetch_recent(self, **_kw: Any) -> list:
        return []


def _announce(d: Any) -> str:
    relay = _Relay()
    env = {"id": "e", "message_id": "m", "room": "build", "from_name": "arav",
           "content": "hello", "message_type": "chat"}
    assert asyncio.run(d._limited(relay, env))
    return relay.posted[0]["content"]


# ── the reading vs the pause ────────────────────────────────────────────


def test_retry_in_gives_the_exact_reset_and_a_capped_pause() -> None:
    reading, retry = limits.read_limit_message("quota hit, retry in 2h", T)
    assert (reading.kind, reading.source, reading.window) == ("observed", "msg.retry_in", "limit")
    assert reading.resets_at == T + 2 * 3600 and reading.seen_at == T
    assert retry == 2 * 3600
    reading, retry = limits.read_limit_message("retry in 30h", T)
    assert reading.resets_at == T + 30 * 3600  # the vendor's time, uncapped
    assert retry == limits.LIMIT_MAX_S  # our pause is capped at 24 h


def test_try_again_at_reset_has_no_padding_but_the_pause_does() -> None:
    msg = "You've hit your usage limit ... try again at 1:46 AM."
    reading, retry = limits.read_limit_message(msg, T, local_now=datetime(2026, 10, 17, 0, 50))
    assert reading.source == "msg.try_again_at" and reading.resets_at == T + 56 * 60
    assert retry == 56 * 60 + 60


def test_no_time_or_an_untrusted_time_is_a_guess_with_no_reset() -> None:
    for msg in ("quota exceeded", "retry in 0s", "retry in 999h"):
        reading, retry = limits.read_limit_message(msg, T)
        assert (reading.kind, reading.source, reading.resets_at) == ("guess", "msg.no_time", None)
    assert limits.read_limit_message("quota exceeded", T)[1] == limits.LIMIT_DEFAULT_S
    # an untrusted reset still pauses on 0.6.5's numbers
    assert limits.read_limit_message("retry in 999h", T)[1] == limits.LIMIT_MAX_S


def test_the_source_is_an_id_never_the_vendors_text() -> None:
    secret = "quota exceeded for key sk-live-abc123, retry in 5m"
    reading, _ = limits.read_limit_message(secret, T)
    assert reading.source in limits.SOURCE_KIND and "sk-live" not in repr(reading)


def test_a_later_guess_does_not_overwrite_a_live_observed_reset() -> None:
    observed, _ = limits.read_limit_message("retry in 4h", T)
    guess, _ = limits.read_limit_message("quota exceeded", T + 60)
    assert limits.merge(observed, guess, T + 60) is observed
    assert limits.merge(observed, guess, T + 5 * 3600) is guess  # that reset has passed
    newer, _ = limits.read_limit_message("retry in 1h", T + 60)
    assert limits.merge(observed, newer, T + 60) is newer


# ── cooldowns are ours, not quota ───────────────────────────────────────


def test_slow_start_and_no_route_cooldowns_are_never_quota() -> None:
    for reason in ("slow_start", "no_route"):
        d = _daemon()
        d.adapter._cool_down(reason)
        assert d.adapter.limited_until > time.time() and d.adapter.limit is None
        assert d.adapter.pause_reason == reason
        assert "waiting_on" not in d._presence_extra("active")
        line = _announce(d)
        assert "paused by Quorus" in line and "quota" not in line


def test_a_cooldown_never_shortens_a_longer_quota_pause() -> None:
    d = _daemon()
    d.adapter.note_limit("retry in 3h")
    d.adapter._cool_down("slow_start")
    assert d.adapter.pause_reason == "quota" and d.adapter.limited_until > time.time() + 2 * 3600


# ── what the room is told ───────────────────────────────────────────────


def test_an_observed_limit_shows_the_vendor_reset_not_our_retry() -> None:
    d = _daemon()
    d.adapter.note_limit("You've hit your usage limit, retry in 2h30m")
    assert d._presence_extra("active")["waiting_on"] == "quota"
    line = _announce(d)
    assert "out of quota for about 2.5 h (the vendor's reset time)" in line and "?" not in line


def test_a_guess_is_never_shown_without_a_question_mark() -> None:
    d = _daemon()
    d.adapter.note_limit("quota exceeded")
    assert d.adapter.limit.kind == "guess"
    assert "out of quota for about 30 min?" in _announce(d)


# ── clearing ────────────────────────────────────────────────────────────


def test_a_good_turn_clears_the_limit_and_a_fresh_daemon_has_none() -> None:
    d = _daemon()
    assert d.adapter.limit is None and d.adapter.current_limit() is None
    d.adapter.note_limit("retry in 2h")
    d._note_outcome("⚠ gemini: the vendor reports the account is out of quota.")
    assert d.adapter.limit is not None  # a failed turn keeps it
    d._note_outcome("fixed it")
    assert d.adapter.limit is None


def test_a_model_rotation_clears_the_reading_and_the_pause() -> None:
    free = reflexd.reflexd_models.OPENCODE_FREE_MODELS
    adapter = reflexd.HeadlessAdapter()
    adapter.model = free[0]

    async def once(*_a: Any, **_k: Any) -> str:
        if adapter.model == free[0]:
            adapter.note_limit("rate limit, retry in 2h")
            return "⚠ opencode: the vendor rate-limited this account; the wake was dropped."
        return f"fixed it on {adapter.model}"

    adapter._opencode_once = once  # type: ignore[method-assign]
    asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert adapter.model == free[1]
    assert (adapter.limit, adapter.limited_until, adapter.pause_reason) == (None, 0.0, "quota")


# ── the two clock-advance gates (Codex, plan GO on ab26832d) ────────────


def test_clock_advance_an_observed_limit_lasts_until_its_reset() -> None:
    adapter = reflexd.HeadlessAdapter()
    adapter.limit, retry = limits.read_limit_message("retry in 21h", T)
    adapter.limited_until, adapter.pause_reason = T + retry, "quota"
    assert adapter.current_limit(T + 3 * 3600) is not None  # far past any 1 h usage age
    assert adapter.current_limit(T + 21 * 3600 - 1) is not None
    assert adapter.current_limit(T + 21 * 3600 + 1) is None
    assert adapter.limit is None  # dropped, not just hidden


def test_clock_advance_an_unknown_reset_lasts_exactly_as_long_as_the_pause() -> None:
    adapter = reflexd.HeadlessAdapter()
    adapter.limit, retry = limits.read_limit_message("quota exceeded", T)
    adapter.limited_until, adapter.pause_reason = T + retry, "quota"
    assert retry == 30 * 60
    assert adapter.current_limit(T + 29 * 60) is not None
    assert adapter.current_limit(T + 31 * 60) is None
    # and a reset-less reading never outlives a pause that stopped being quota
    adapter.limit, _ = limits.read_limit_message("quota exceeded", T)
    adapter.pause_reason = "slow_start"
    assert adapter.current_limit(T + 60) is None
