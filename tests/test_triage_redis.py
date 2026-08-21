"""R3 (WAKE_REBUILD_SPEC): Redis-shared triage auction.

The process-local auction elects one winner PER PROCESS — two uvicorn
workers each crown their own winner and one mention gets two replies.
With Redis configured, the claim is a single ``SET NX``: exactly one
winner machine-wide, credits applied exactly once, idempotent re-claims.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

try:
    from fakeredis import aioredis as fakeredis_aio
except ImportError:  # pragma: no cover
    fakeredis_aio = None

from quorus.relay import _reset_state, app
from quorus.routes import triage_redis as tr

pytestmark = pytest.mark.skipif(
    fakeredis_aio is None, reason="fakeredis not installed"
)

HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch):
    r = fakeredis_aio.FakeRedis()
    monkeypatch.setattr(tr, "redis_or_none", lambda: r)
    return r


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _room_with(client: AsyncClient, members: list[str]) -> str:
    resp = await client.post(
        "/rooms", json={"name": "r3", "created_by": members[0]}, headers=HEADERS
    )
    rid = resp.json()["id"]
    for m in members[1:]:
        await client.post(
            f"/rooms/{rid}/join", json={"participant": m}, headers=HEADERS
        )
    return rid


async def test_concurrent_claims_elect_exactly_one_winner(fake_redis):
    """The core R3 guarantee at the store layer: N concurrent claim races
    against one Redis → one SET NX succeeds, every caller sees the SAME
    claim, credits applied exactly once."""
    for who, bid in (("a-claude", 0.9), ("b-claude", 0.7)):
        await tr.record_bid(
            fake_redis, tid="t", rid="room", mid="m1",
            participant=who, bid=bid, reason="test", ttl_seconds=30,
        )

    def factory(bids, winner, winner_bid, credits):
        import uuid
        return {"claimed": True, "winner": winner, "bid": winner_bid,
                "claim_token": str(uuid.uuid4()), "expires_at": "x",
                "candidates": sorted(bids), "fairness_credit": credits}

    results = await asyncio.gather(*[
        tr.try_claim(fake_redis, tid="t", rid="room", mid="m1",
                     claim_payload_factory=factory)
        for _ in range(8)
    ])
    payloads = [p for p, _, _ in results if p is not None]
    fresh = [is_fresh for _, _, is_fresh in results]
    assert sum(fresh) == 1, "exactly one caller may report a fresh claim"
    assert len(payloads) == 8
    tokens = {p["claim_token"] for p in payloads}
    winners = {p["winner"] for p in payloads}
    assert len(tokens) == 1, "all callers must see the SAME claim"
    assert winners == {"a-claude"}
    # Credits applied exactly once: winner −1.0, loser +0.25.
    assert await tr.get_credit(fake_redis, "t", "a-claude") == pytest.approx(-1.0)
    assert await tr.get_credit(fake_redis, "t", "b-claude") == pytest.approx(0.25)


async def test_route_level_bid_claim_via_redis(client, fake_redis):
    rid = await _room_with(client, ["arav", "a-claude", "b-claude"])
    mid = "msg-route-1"
    for who, bid in (("a-claude", 0.9), ("b-claude", 0.7)):
        resp = await client.post("/v1/bid", json={
            "room_id": rid, "message_id": mid, "participant": who,
            "bid": bid, "reason": "t",
        }, headers=HEADERS)
        assert resp.status_code == 200, resp.text
    assert resp.json()["leader"] == "a-claude"

    c1 = await client.post("/v1/claim", json={"room_id": rid, "message_id": mid},
                           headers=HEADERS)
    c2 = await client.post("/v1/claim", json={"room_id": rid, "message_id": mid},
                           headers=HEADERS)
    assert c1.status_code == c2.status_code == 200
    assert c1.json()["winner"] == "a-claude"
    assert c1.json()["claim_token"] == c2.json()["claim_token"], "idempotent"
    # Redis actually holds the claim (not process memory).
    stored = await fake_redis.get("triage:claim:_legacy:%s:%s" % (rid, mid))
    assert stored is not None and json.loads(stored)["winner"] == "a-claude"


async def test_claim_without_bids_404s_via_redis(client, fake_redis):
    rid = await _room_with(client, ["arav"])
    resp = await client.post("/v1/claim", json={
        "room_id": rid, "message_id": "no-bids",
    }, headers=HEADERS)
    assert resp.status_code == 404


async def test_fairness_credit_shapes_next_auction(fake_redis):
    """Loser's +0.25 credit lets an equal bid win the NEXT round."""
    for mid in ("m1", "m2"):
        for who in ("a-claude", "b-claude"):
            await tr.record_bid(
                fake_redis, tid="t", rid="room", mid=mid,
                participant=who, bid=0.5, reason="t", ttl_seconds=30,
            )

    def factory(bids, winner, winner_bid, credits):
        return {"claimed": True, "winner": winner, "bid": winner_bid,
                "claim_token": "tok-" + winner, "expires_at": "x",
                "candidates": sorted(bids), "fairness_credit": credits}

    p1, _, _ = await tr.try_claim(fake_redis, tid="t", rid="room", mid="m1",
                                  claim_payload_factory=factory)
    p2, _, _ = await tr.try_claim(fake_redis, tid="t", rid="room", mid="m2",
                                  claim_payload_factory=factory)
    assert p1["winner"] != p2["winner"], "credits must rotate equal bidders"


async def test_phase1_primitives_survive_service_restart(
    monkeypatch: pytest.MonkeyPatch,
):
    """R4: memory/capabilities/tool-catalog hydrate from Redis after a
    process restart (simulated by constructing fresh service instances
    against the same fakeredis)."""
    from quorus.services import p1_persistence
    from quorus.services.capability_svc import CapabilitySvc
    from quorus.services.persistent_memory_svc import PersistentMemorySvc
    from quorus.services.tool_catalog_svc import ToolCatalogSvc

    r = fakeredis_aio.FakeRedis()
    monkeypatch.setattr(p1_persistence, "get_redis_or_none", lambda: r)

    mem1, cap1, tools1 = PersistentMemorySvc(), CapabilitySvc(), ToolCatalogSvc()
    await mem1.set("t", "arav-claude", "room", "plan", {"step": 1})
    await cap1.publish("t", "arav-claude", {"capabilities": ["python"]})
    await tools1.register(
        "t", "room", name="run_pytest", url="wrap://shell:pytest",
        registered_by="arav-claude",
    )

    # "Restart": brand-new instances, empty in-memory state.
    mem2, cap2, tools2 = PersistentMemorySvc(), CapabilitySvc(), ToolCatalogSvc()
    got = await mem2.get("t", "arav-claude", "room", "plan")
    assert got is not None and got["value"] == {"step": 1}
    manifest = await cap2.get("t", "arav-claude")
    assert manifest is not None and manifest["capabilities"] == ["python"]
    tools = await tools2.list("t", "room")
    assert [t["name"] for t in tools] == ["run_pytest"]

    # Deletes propagate too — a third restart must not resurrect them.
    assert await mem2.delete("t", "arav-claude", "room", "plan") is True
    mem3 = PersistentMemorySvc()
    assert await mem3.get("t", "arav-claude", "room", "plan") is None


# ── code-review regressions (2026-08-21) ────────────────────────────────────

async def test_lapsed_window_starts_empty(fake_redis, monkeypatch):
    """A bid window that expires unclaimed must NOT carry its old bids into
    the next auction: the in-memory path builds a fresh window, and leaving
    them let an hour-old bidder win an auction it never entered."""
    await tr.record_bid(
        fake_redis, tid="t", rid="room", mid="m1",
        participant="stale-claude", bid=0.9, reason="old", ttl_seconds=1,
    )
    # Force the window past its deadline without waiting.
    await fake_redis.hset(
        tr._bids_key("t", "room", "m1"), tr._EXPIRES_FIELD,
        "2000-01-01T00:00:00+00:00",
    )
    snap = await tr.record_bid(
        fake_redis, tid="t", rid="room", mid="m1",
        participant="fresh-claude", bid=0.5, reason="new", ttl_seconds=30,
    )
    assert snap["leader"] == "fresh-claude", (
        "the stale 0.9 bid must not win the new window"
    )
    bids, _ = await tr._load_bids(fake_redis, "t", "room", "m1")
    assert set(bids) == {"fresh-claude"}


async def test_only_the_race_winner_reports_fresh(fake_redis):
    """is_fresh gates the wake broadcast — a re-claim must not re-wake."""
    await tr.record_bid(
        fake_redis, tid="t", rid="room", mid="m1",
        participant="a-claude", bid=0.9, reason="t", ttl_seconds=30,
    )

    def factory(bids, winner, winner_bid, credits):
        return {"claimed": True, "winner": winner, "bid": winner_bid,
                "claim_token": "tok", "expires_at": "x",
                "candidates": sorted(bids), "fairness_credit": credits}

    first, _, fresh1 = await tr.try_claim(
        fake_redis, tid="t", rid="room", mid="m1", claim_payload_factory=factory)
    second, _, fresh2 = await tr.try_claim(
        fake_redis, tid="t", rid="room", mid="m1", claim_payload_factory=factory)
    assert fresh1 is True and fresh2 is False
    assert first["claim_token"] == second["claim_token"]


async def test_credits_come_from_the_increment_itself(fake_redis):
    """The claim reports the post-increment value HINCRBYFLOAT returned,
    not a racy second read."""
    for who, bid in (("a-claude", 0.9), ("b-claude", 0.4)):
        await tr.record_bid(fake_redis, tid="t", rid="room", mid="m1",
                            participant=who, bid=bid, reason="t",
                            ttl_seconds=30)

    def factory(bids, winner, winner_bid, credits):
        return {"claimed": True, "winner": winner, "bid": winner_bid,
                "claim_token": "tok", "expires_at": "x",
                "candidates": sorted(bids), "fairness_credit": credits}

    payload, _, _ = await tr.try_claim(
        fake_redis, tid="t", rid="room", mid="m1", claim_payload_factory=factory)
    assert payload["fairness_credit"]["a-claude"] == pytest.approx(-1.0)
    assert payload["fairness_credit"]["b-claude"] == pytest.approx(0.25)


def test_redis_fallback_only_when_unconfigured(monkeypatch):
    """Swallowing every exception here would silently drop a configured
    deployment back to the double-winner in-memory auction."""
    from quorus.backends import redis_client

    monkeypatch.setattr(redis_client, "_redis", None)
    assert tr.redis_or_none() is None
    sentinel = object()
    monkeypatch.setattr(redis_client, "_redis", sentinel)
    assert tr.redis_or_none() is sentinel


# ── R4 review regressions (2026-08-21) ──────────────────────────────────────

async def test_work_queue_claims_survive_restart(monkeypatch):
    """R4's headline claim, which the review found unimplemented: the mirror
    was write-only, so a restart still lost every claim."""
    from quorus.services.work_queue_svc import WorkQueueSvc

    r = fakeredis_aio.FakeRedis()
    svc1 = WorkQueueSvc(redis_conn=r)
    task = await svc1.add(
        "t", "room", summary="ship the relay", requested_by="arav")
    await svc1.claim("t", "room", task_id=task["task_id"], actor="arav-claude")

    svc2 = WorkQueueSvc(redis_conn=r)  # "restart"
    restored = await svc2.list("t", "room")
    assert [x["summary"] for x in restored] == ["ship the relay"]
    assert restored[0]["claimed_by"] == "arav-claude"


async def test_terminal_tasks_leave_the_mirror(monkeypatch):
    """Completed work has nothing to restore; keeping it grew the hash
    forever (expire() re-persisted every timed-out task)."""
    from quorus.services.work_queue_svc import WorkQueueSvc

    r = fakeredis_aio.FakeRedis()
    svc = WorkQueueSvc(redis_conn=r)
    task = await svc.add("t", "room", summary="temp", requested_by="arav")
    tid_ = task["task_id"]
    await svc.claim("t", "room", task_id=tid_, actor="arav-claude")
    assert await r.hlen("work_queue:t:room") == 1
    await svc.complete("t", "room", task_id=tid_, actor="arav-claude")
    assert await r.hlen("work_queue:t:room") == 0, "terminal task must be dropped"


async def test_work_queue_mirror_has_a_ttl():
    """An abandoned room's hash must not live forever."""
    from quorus.services.work_queue_svc import WorkQueueSvc

    r = fakeredis_aio.FakeRedis()
    svc = WorkQueueSvc(redis_conn=r)
    await svc.add("t", "room", summary="x", requested_by="arav")
    ttl = await r.ttl("work_queue:t:room")
    assert ttl > 0, "mirror key must expire"


async def test_reset_clears_mirror_and_hydration(monkeypatch):
    """Reset used to leave the bucket marked hydrated (next read served
    empty) AND leave the mirror intact (data returned on restart)."""
    from quorus.services import p1_persistence
    from quorus.services.persistent_memory_svc import PersistentMemorySvc

    r = fakeredis_aio.FakeRedis()
    monkeypatch.setattr(p1_persistence, "get_redis_or_none", lambda: r)

    svc = PersistentMemorySvc()
    await svc.set("t", "arav-claude", "room", "plan", {"step": 1})
    await svc.reset("t")
    assert await svc.get("t", "arav-claude", "room", "plan") is None
    # And it must not resurrect in a fresh process.
    assert await PersistentMemorySvc().get(
        "t", "arav-claude", "room", "plan") is None


async def test_transient_redis_error_does_not_pin_empty_state(monkeypatch):
    """A blip on first touch used to mark the bucket hydrated forever, so
    the service silently served empty state for the process lifetime."""
    from quorus.services import p1_persistence
    from quorus.services.persistent_memory_svc import PersistentMemorySvc

    r = fakeredis_aio.FakeRedis()
    monkeypatch.setattr(p1_persistence, "get_redis_or_none", lambda: r)
    seed = PersistentMemorySvc()
    await seed.set("t", "arav-claude", "room", "plan", {"step": 7})

    svc = PersistentMemorySvc()
    calls = {"n": 0}
    real_hydrate = p1_persistence.hydrate

    async def flaky(ns_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return {}, False  # transient failure
        return await real_hydrate(ns_key)

    monkeypatch.setattr(p1_persistence, "hydrate", flaky)
    assert await svc.get("t", "arav-claude", "room", "plan") is None  # blip
    got = await svc.get("t", "arav-claude", "room", "plan")  # retried
    assert got is not None and got["value"] == {"step": 7}
