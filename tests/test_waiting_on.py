"""What a not-working agent is waiting on, for the "needs you" board (2026-10-17).

Before, "waiting for its plan slot", "out of quota" and "stuck" existed only as
chat lines a board would have to guess from. Presence now carries them.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


def _daemon():
    cfg = reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                participant_name="a-codex", spawn_enabled=False)
    return reflexd.Reflexd(cfg)


def test_daemon_reports_quota_pacing_and_stuck() -> None:
    d = _daemon()
    assert d._presence_extra("active") == {}
    d.adapter.limited_until = time.time() + 600
    extra = d._presence_extra("active")
    assert extra["waiting_on"] == "quota" and extra["waiting_until"] > time.time()
    d.adapter.limited_until = 0.0
    d._waiting = ("pacing", time.time() + 120)
    assert d._presence_extra("active")["waiting_on"] == "pacing"
    d._waiting = ("pacing", time.time() - 1)  # the slot has come
    assert d._presence_extra("active") == {}
    # busy shows the step, never a stale wait
    d._waiting, d._activity = ("pacing", time.time() + 99), "editing relay.py"
    assert d._presence_extra("busy") == {"activity": "editing relay.py"}


def test_a_failed_turn_is_stuck_until_a_good_one() -> None:
    d = _daemon()
    d._note_outcome("⚠ codex: the CLI exited 1 (unknown error)")
    assert d._waiting == ("stuck", 0.0)
    d._note_outcome("⚠ opencode: the vendor rate-limited this account")  # a limit isn't stuck
    assert d._waiting == ("stuck", 0.0)
    d._note_outcome("✅ fixed it in abc1234")
    assert d._waiting == ("", 0.0)


@pytest.fixture
async def client():
    _reset_state()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    _reset_state()


async def test_relay_stores_and_returns_waiting(client: AsyncClient) -> None:
    h = {"Authorization": "Bearer test-secret"}
    until = time.time() + 900
    r = await client.post("/heartbeat", headers=h, json={
        "instance_name": "a-codex", "status": "active", "waiting_on": "quota",
        "waiting_until": until})
    assert r.status_code == 200
    (p,) = [x for x in (await client.get("/presence", headers=h)).json()
            if x["name"] == "a-codex"]
    assert p["waiting_on"] == "quota" and abs(p["waiting_until"] - until) < 1
    # A clear heartbeat clears it.
    await client.post("/heartbeat", headers=h, json={"instance_name": "a-codex"})
    (p,) = [x for x in (await client.get("/presence", headers=h)).json()
            if x["name"] == "a-codex"]
    assert p["waiting_on"] == "" and p["waiting_until"] == 0.0


async def test_relay_refuses_free_text_waiting(client: AsyncClient) -> None:
    h = {"Authorization": "Bearer test-secret"}
    r = await client.post("/heartbeat", headers=h, json={
        "instance_name": "a-codex", "waiting_on": "<img src=x onerror=alert(1)>"})
    assert r.status_code == 422
    r = await client.post("/heartbeat", headers=h, json={
        "instance_name": "a-codex", "waiting_on": "quota", "waiting_until": -5})
    assert r.status_code == 422
