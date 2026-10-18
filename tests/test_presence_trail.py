"""Each agent's last steps, for the mission-control panels (2026-10-18)."""

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from quorus.backends.memory import InMemoryPresenceBackend
from quorus.backends.redis_backends import RedisPresenceBackend
from quorus.relay import _reset_state, app
from quorus.services import presence_trail


def test_trail_keeps_new_steps_newest_last_and_caps() -> None:
    t = presence_trail.extend(None, "reading relay.py", 1.0)
    t = presence_trail.extend(t, "reading relay.py", 2.0)  # same step: not repeated
    t = presence_trail.extend(t, "", 3.0)  # idle heartbeat: nothing to add
    t = presence_trail.extend(t, "running tests", 4.0)
    assert t == [[1.0, "reading relay.py"], [4.0, "running tests"]]
    for i in range(50):
        t = presence_trail.extend(t, f"step {i}", 10.0 + i)
    assert len(t) == presence_trail.TRAIL_MAX and t[-1][1] == "step 49"
    assert presence_trail.public('[[1, "a"]]') == [{"t": 1.0, "step": "a"}]
    assert presence_trail.load("not json") == [] and presence_trail.load(None) == []


@pytest.mark.parametrize("redis", [False, True])
async def test_both_backends_keep_the_trail(redis) -> None:
    connection = fakeredis.aioredis.FakeRedis(decode_responses=True)
    backend = RedisPresenceBackend(connection) if redis else InMemoryPresenceBackend()
    try:
        for step in ("editing calc.py", "running tests", "running tests", "committing"):
            await backend.heartbeat("t", "a-claude", "busy", "r", activity=step)
        entry = (await backend.list_all("t", 90))[0]
        steps = [s["step"] for s in presence_trail.public(entry["trail"])]
        assert steps == ["editing calc.py", "running tests", "committing"]
    finally:
        await connection.aclose()


async def test_presence_serves_the_trail_only_when_asked() -> None:
    _reset_state()
    h = {"Authorization": "Bearer test-secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for step in ("reading board.py", "running tests"):
            r = await c.post("/heartbeat", headers=h, json={
                "instance_name": "a-claude", "status": "busy", "room": "r", "activity": step})
            assert r.status_code == 200, r.text
        plain = (await c.get("/presence", headers=h)).json()
        rich = (await c.get("/presence?trail=1", headers=h)).json()
    assert "trail" not in plain[0]
    assert [s["step"] for s in rich[0]["trail"]] == ["reading board.py", "running tests"]
    _reset_state()
