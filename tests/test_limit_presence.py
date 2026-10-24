"""Limit-status slice 2: ``pause`` and ``readings`` on presence.

Design: docs/internal/bets/limit-status.md rev 3, "Bounded fields" and the
malformed-reading gates. Both fields are optional. The relay drops a bad
reading and keeps the rest of the heartbeat, checks again on read so an
expired reading isn't shown, and never takes vendor text.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from quorus import limit_status
from quorus.relay import _reset_state, app
from quorus.routes import presence as presence_route
from quorus.routes.models import HeartbeatRequest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)

NOW = 1_800_000_000.0
H = {"Authorization": "Bearer test-secret"}


def _observed(**kw: Any) -> dict[str, Any]:
    return {"window": "limit", "kind": "observed", "source": "msg.retry_in",
            "used_pct": None, "resets_at": NOW + 7200, "seen_at": NOW - 10, **kw}


def _usage(**kw: Any) -> dict[str, Any]:
    return {"window": "5h", "kind": "reported", "source": "claude.statusline",
            "used_pct": 72.0, "resets_at": NOW + 3600, "seen_at": NOW - 60, **kw}


QUOTA = {"reason": "quota", "until": NOW + 1800}


# ── the validator ───────────────────────────────────────────────────────


def test_valid_readings_and_pause_are_kept() -> None:
    out = limit_status.clean(QUOTA, [_observed(), _usage(), _usage(window="7d")], NOW)
    assert out["pause"] == QUOTA
    assert [r["window"] for r in out["readings"]] == ["limit", "5h", "7d"]
    guess = {"window": "limit", "kind": "guess", "source": "msg.no_time", "seen_at": NOW}
    assert limit_status.clean(QUOTA, [guess], NOW)["readings"][0]["resets_at"] is None


@pytest.mark.parametrize("bad", [
    _usage(used_pct=-1), _usage(used_pct=101), _usage(used_pct=float("nan")),
    _usage(used_pct=True), _usage(used_pct=None), _observed(used_pct=50.0),
    _observed(resets_at=NOW - 20), _observed(resets_at=NOW + 30 * 86400),
    _observed(resets_at=float("inf")), _observed(resets_at="soon"),
    _observed(seen_at=NOW + 3600), _observed(seen_at=True), _observed(seen_at=None),
    _observed(window="1h"), _observed(kind="estimate"), _observed(source="msg.other"),
    _observed(kind="guess"), _usage(kind="observed"), _usage(source="msg.retry_in"),
    _observed(window="5h"), _usage(window="limit"),
    _observed(source="stderr: retry in 2h, key sk-live-abc"),
    _observed(note="retry in 2h"),  # no keys outside the schema
    {"window": "limit", "kind": "guess", "source": "msg.no_time", "seen_at": NOW,
     "resets_at": NOW + 60},  # a guess has no reset time
    "retry in 2h", None, 7, [],
])
def test_each_malformed_reading_is_dropped_alone(bad: Any) -> None:
    out = limit_status.clean(QUOTA, [bad, _usage()], NOW)
    assert out["pause"] == QUOTA and out["readings"] == [limit_status.clean(
        None, [_usage()], NOW)["readings"][0]]


def test_two_readings_for_one_window_or_more_than_three_are_dropped() -> None:
    assert "readings" not in limit_status.clean(None, [_usage(), _usage(used_pct=10.0)], NOW)
    four = [_observed(), _usage(), _usage(window="7d"), _usage(window="7d")]
    assert "readings" not in limit_status.clean(None, four, NOW)
    assert limit_status.clean(None, {"window": "5h"}, NOW) == {}  # not a list


@pytest.mark.parametrize("bad", [
    {"reason": "vendor", "until": NOW + 60}, {"reason": "quota", "until": NOW - 1},
    {"reason": "quota", "until": float("nan")}, {"reason": "quota", "until": True},
    {"reason": "quota", "until": NOW + 60, "why": "x"}, {"reason": "quota"}, "quota", 1,
])
def test_a_malformed_pause_is_dropped(bad: Any) -> None:
    assert "pause" not in limit_status.clean(bad, [], NOW)


def test_usage_ages_out_but_a_live_limit_does_not() -> None:
    assert "readings" not in limit_status.clean(None, [_usage(seen_at=NOW - 7200)], NOW)
    old_limit = _observed(seen_at=NOW - 3 * 3600, resets_at=NOW + 18 * 3600)
    assert limit_status.clean(None, [old_limit], NOW)["readings"]


def test_a_free_busy_pause_is_a_valid_pause_but_never_a_quota() -> None:
    """keyless-answer.md §3.1: the daemon's pause for opencode's free-tier 429."""
    pause = {"reason": "free_busy", "until": NOW + 900}
    assert limit_status.clean_pause(pause, NOW) == pause
    guess = {"window": "limit", "kind": "guess", "source": "msg.no_time", "seen_at": NOW}
    assert "readings" not in limit_status.clean(pause, [guess], NOW)  # not a quota pause
    assert limit_status.clean_pause({"reason": "free-busy", "until": NOW + 9}, NOW) is None


def test_a_reset_less_limit_needs_a_live_quota_pause() -> None:
    guess = {"window": "limit", "kind": "guess", "source": "msg.no_time", "seen_at": NOW}
    assert "readings" not in limit_status.clean(None, [guess], NOW)
    assert "readings" not in limit_status.clean(
        {"reason": "slow_start", "until": NOW + 60}, [guess], NOW)
    assert "readings" not in limit_status.clean(
        {"reason": "quota", "until": NOW - 1}, [guess], NOW)


# ── the relay ───────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    _reset_state()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    _reset_state()


async def _presence(client: AsyncClient, name: str) -> dict[str, Any]:
    (p,) = [x for x in (await client.get("/presence", headers=H)).json() if x["name"] == name]
    return p


async def test_relay_keeps_the_heartbeat_and_drops_only_the_bad_reading(
        client: AsyncClient) -> None:
    now = time.time()
    good = {"window": "limit", "kind": "observed", "source": "msg.retry_in",
            "resets_at": now + 7200, "seen_at": now}
    r = await client.post("/heartbeat", headers=H, json={
        "instance_name": "a-codex", "waiting_on": "quota", "waiting_until": now + 1800,
        "pause": {"reason": "quota", "until": now + 1800},
        "readings": [good, {**good, "window": "5h", "used_pct": 500}]})
    assert r.status_code == 200, r.text
    p = await _presence(client, "a-codex")
    assert p["waiting_on"] == "quota" and p["pause"]["reason"] == "quota"
    assert [x["window"] for x in p["readings"]] == ["limit"]
    assert p["readings"][0]["resets_at"] == good["resets_at"]


async def test_relay_accepts_garbage_limit_fields_without_a_422(client: AsyncClient) -> None:
    r = await client.post("/heartbeat", headers=H, json={
        "instance_name": "a-codex", "pause": "<img src=x>", "readings": {"a": [1, 2]}})
    assert r.status_code == 200, r.text
    p = await _presence(client, "a-codex")
    assert "pause" not in p and "readings" not in p


async def test_an_expired_reading_is_not_shown_on_read(client: AsyncClient,
                                                       monkeypatch) -> None:
    now = time.time()
    await client.post("/heartbeat", headers=H, json={
        "instance_name": "a-codex", "pause": {"reason": "quota", "until": now + 60},
        "readings": [{"window": "limit", "kind": "observed", "source": "msg.retry_in",
                      "resets_at": now + 30, "seen_at": now}]})
    assert (await _presence(client, "a-codex"))["readings"]
    # inside the 90 s presence timeout, so the agent is still online
    monkeypatch.setattr(presence_route.time, "time", lambda: now + 31)
    p = await _presence(client, "a-codex")
    assert "readings" not in p and p["pause"]["reason"] == "quota"
    monkeypatch.setattr(presence_route.time, "time", lambda: now + 61)
    assert "pause" not in await _presence(client, "a-codex")


def test_unknown_heartbeat_fields_are_ignored_not_refused() -> None:
    """Why an old relay takes a new daemon's heartbeat: extra fields are ignored."""
    assert HeartbeatRequest.model_config.get("extra", "ignore") == "ignore"
    req = HeartbeatRequest.model_validate({"instance_name": "a-codex", "budget": {"x": 1}})
    assert req.instance_name == "a-codex"


# ── the daemon ──────────────────────────────────────────────────────────


def _daemon() -> Any:
    cfg = reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                participant_name="a-codex", spawn_enabled=False)
    return reflexd.Reflexd(cfg)


def test_daemon_sends_pause_and_reading_that_the_relay_accepts() -> None:
    d = _daemon()
    assert d._presence_extra("active") == {}
    d.adapter.note_limit("You've hit your usage limit, retry in 2h")
    extra = d._presence_extra("active")
    assert extra["waiting_on"] == "quota" and extra["pause"]["reason"] == "quota"
    cleaned = limit_status.clean(extra["pause"], extra["readings"], time.time())
    assert cleaned["readings"][0]["source"] == "msg.retry_in"
    assert d._presence_extra("busy") == {}  # busy shows the step only


def test_daemon_cooldown_sends_its_pause_but_no_quota() -> None:
    d = _daemon()
    d.adapter._cool_down("no_route")
    extra = d._presence_extra("active")
    assert "waiting_on" not in extra and "readings" not in extra
    assert extra["pause"]["reason"] == "no_route"


def test_heartbeat_body_omits_empty_limit_fields() -> None:
    sent: list[dict] = []

    class _Resp:
        def raise_for_status(self) -> None:
            pass

    class _Http:
        async def post(self, path: str, *, headers: dict, json: dict) -> _Resp:
            sent.append(json)
            return _Resp()

    relay = reflexd.RelayClient.__new__(reflexd.RelayClient)
    relay._client = _Http()  # type: ignore[assignment]

    async def headers() -> dict:
        return {}

    relay._headers = headers  # type: ignore[method-assign]
    asyncio.run(relay.post_heartbeat(participant="a-codex"))
    asyncio.run(relay.post_heartbeat(participant="a-codex", pause=QUOTA,
                                     readings=[_observed()]))
    assert "pause" not in sent[0] and "readings" not in sent[0]
    assert sent[1]["pause"] == QUOTA and sent[1]["readings"][0]["source"] == "msg.retry_in"


# ── Codex on 70083186: wrong types and huge numbers must not raise ──────

HUGE = 10 ** 400  # valid JSON; float() of it raises OverflowError


@pytest.mark.parametrize("bad", [
    _observed(window=["limit"]), _observed(window={"w": 1}), _observed(kind=["observed"]),
    _observed(source={"id": "msg.retry_in"}), _observed(seen_at=HUGE),
    _observed(resets_at=HUGE), _observed(seen_at=-HUGE), _usage(used_pct=HUGE),
    _usage(used_pct=-HUGE),
])
def test_wrong_types_and_huge_numbers_are_dropped_not_raised(bad: Any) -> None:
    out = limit_status.clean(QUOTA, [bad, _usage()], NOW)
    assert [r["window"] for r in out["readings"]] == ["5h"]


@pytest.mark.parametrize("bad", [
    {"reason": ["quota"], "until": NOW + 60}, {"reason": {"q": 1}, "until": NOW + 60},
    {"reason": "quota", "until": HUGE}, {"reason": "quota", "until": -HUGE},
])
def test_a_pause_with_wrong_types_or_huge_numbers_is_dropped(bad: Any) -> None:
    assert limit_status.clean(bad, [_usage()], NOW).get("pause") is None


def test_the_validator_never_raises_on_any_json_shape() -> None:
    import random

    rng = random.Random(7)
    leaves: list[Any] = [None, True, 0, -1, 1.5, HUGE, -HUGE, "", "quota", "limit", "5h",
                         "observed", "msg.retry_in", NOW, NOW + 60]

    def value(depth: int) -> Any:
        if depth > 2 or rng.random() < 0.6:
            return rng.choice(leaves)
        if rng.random() < 0.5:
            return [value(depth + 1) for _ in range(rng.randint(0, 3))]
        keys = ["window", "kind", "source", "used_pct", "resets_at", "seen_at", "reason",
                "until"]
        return {k: value(depth + 1) for k in rng.sample(keys, rng.randint(0, len(keys)))}

    for _ in range(3000):
        limit_status.clean(value(0), value(0), NOW)
        limit_status.clean(value(0), [value(1) for _ in range(rng.randint(0, 3))], NOW)


async def test_relay_drops_wrong_types_and_huge_numbers_and_keeps_the_rest(
        client: AsyncClient) -> None:
    now = time.time()
    good = {"window": "limit", "kind": "observed", "source": "msg.retry_in",
            "resets_at": now + 7200, "seen_at": now}
    r = await client.post("/heartbeat", headers=H, json={
        "instance_name": "a-codex", "status": "idle", "waiting_on": "quota",
        "waiting_until": now + 1800, "model": "gpt-5.5",
        "pause": {"reason": ["quota"], "until": HUGE},
        "readings": [good, {**good, "window": {"w": 1}},
                     {"window": "5h", "kind": "reported", "source": "codex.token_count",
                      "used_pct": HUGE, "seen_at": now}]})
    assert r.status_code == 200, r.text
    p = await _presence(client, "a-codex")
    assert (p["status"], p["waiting_on"], p["model"]) == ("idle", "quota", "gpt-5.5")
    assert "pause" not in p and [x["window"] for x in p["readings"]] == ["limit"]
