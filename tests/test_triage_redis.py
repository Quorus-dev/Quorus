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
    payloads = [p for p, _ in results if p is not None]
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

    p1, _ = await tr.try_claim(fake_redis, tid="t", rid="room", mid="m1",
                               claim_payload_factory=factory)
    p2, _ = await tr.try_claim(fake_redis, tid="t", rid="room", mid="m2",
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
