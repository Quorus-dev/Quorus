from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app
from quorus.routes.triage import reset_triage_state

HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    reset_triage_state()
    yield
    reset_triage_state()
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup_room(client: AsyncClient) -> str:
    room = await client.post(
        "/rooms",
        json={"name": "triage-room", "created_by": "alice"},
        headers=HEADERS,
    )
    assert room.status_code == 200
    room_id = room.json()["id"]
    for participant in ["bob", "carol"]:
        join = await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": participant},
            headers=HEADERS,
        )
        assert join.status_code == 200
    return room_id


async def test_triage_question_returns_response_candidates(client: AsyncClient):
    room_id = await _setup_room(client)

    resp = await client.post(
        "/v1/triage",
        json={
            "room_id": room_id,
            "message_id": "msg-1",
            "from_name": "alice",
            "content": "How should we split this?",
        },
        headers=HEADERS,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "RESPOND"
    assert data["candidates"] == ["bob", "carol"]
    assert data["message_id"] == "msg-1"


async def test_triage_mention_targets_named_candidate_and_sse(client: AsyncClient):
    room_id = await _setup_room(client)
    queue = app.state.sse_service.register_queue("_legacy", "bob")

    try:
        resp = await client.post(
            "/v1/triage",
            json={
                "room_id": room_id,
                "message_id": "msg-mention",
                "from_name": "alice",
                "content": "@bob can you take auth?",
            },
            headers=HEADERS,
        )

        assert resp.status_code == 200
        assert resp.json()["candidates"] == ["bob"]

        message = queue.get_nowait()
        assert message["message_type"] == "wake_intent"
        payload = json.loads(message["content"])
        assert payload["event"] == "wake_intent"
        assert payload["message_id"] == "msg-mention"
        assert payload["candidates"] == ["bob"]
    finally:
        app.state.sse_service.unregister_queue("_legacy", "bob", queue)


async def test_bid_and_claim_select_highest_bid_idempotently(client: AsyncClient):
    room_id = await _setup_room(client)

    bob_bid = await client.post(
        "/v1/bid",
        json={
            "room_id": room_id,
            "message_id": "msg-2",
            "participant": "bob",
            "bid": 0.3,
        },
        headers=HEADERS,
    )
    carol_bid = await client.post(
        "/v1/bid",
        json={
            "room_id": room_id,
            "message_id": "msg-2",
            "participant": "carol",
            "bid": 0.9,
        },
        headers=HEADERS,
    )

    assert bob_bid.status_code == 200
    assert carol_bid.status_code == 200
    assert carol_bid.json()["leader"] == "carol"

    claim = await client.post(
        "/v1/claim",
        json={"room_id": room_id, "message_id": "msg-2"},
        headers=HEADERS,
    )
    again = await client.post(
        "/v1/claim",
        json={"room_id": room_id, "message_id": "msg-2"},
        headers=HEADERS,
    )

    assert claim.status_code == 200
    assert claim.json()["winner"] == "carol"
    assert claim.json()["candidates"] == ["bob", "carol"]
    assert claim.json()["fairness_credit"]["carol"] == -1.0
    assert claim.json()["fairness_credit"]["bob"] == 0.25
    assert again.json()["claim_token"] == claim.json()["claim_token"]


async def test_claim_broadcasts_wake_intent_to_bidders(client: AsyncClient):
    room_id = await _setup_room(client)
    queue = app.state.sse_service.register_queue("_legacy", "bob")
    try:
        for participant, bid in [("bob", 0.4), ("carol", 0.5)]:
            resp = await client.post(
                "/v1/bid",
                json={
                    "room_id": room_id,
                    "message_id": "msg-3",
                    "participant": participant,
                    "bid": bid,
                },
                headers=HEADERS,
            )
            assert resp.status_code == 200

        claim = await client.post(
            "/v1/claim",
            json={"room_id": room_id, "message_id": "msg-3"},
            headers=HEADERS,
        )
        assert claim.status_code == 200

        message = queue.get_nowait()
        assert message["message_type"] == "wake_intent"
        payload = json.loads(message["content"])
        assert payload["event"] == "claim"
        assert payload["winner"] == "carol"
        assert payload["candidates"] == ["bob", "carol"]
    finally:
        app.state.sse_service.unregister_queue("_legacy", "bob", queue)


async def test_claim_without_bids_returns_404(client: AsyncClient):
    room_id = await _setup_room(client)

    resp = await client.post(
        "/v1/claim",
        json={"room_id": room_id, "message_id": "missing"},
        headers=HEADERS,
    )

    assert resp.status_code == 404


async def test_non_bidder_cannot_claim_403(client: AsyncClient):
    """Wave-5 Fix 8 — only an actual bidder may /v1/claim a window.

    Without the bidder gate, any room member could call claim against a
    window they never bid on and receive ``claim_token`` — a privilege-
    escalation vector for downstream tools that trust the token.
    """
    from quorus.auth.tokens import create_jwt
    triage_tenant = "t-triage-fix8"
    triage_slug = "triage-fix8"

    def _hdr(name: str) -> dict[str, str]:
        token = create_jwt(
            sub=name, tenant_id=triage_tenant, tenant_slug=triage_slug,
        )
        return {"Authorization": f"Bearer {token}"}

    # Set up a room with three members.
    room = await client.post(
        "/rooms",
        json={"name": "fix8-room", "created_by": "alice"},
        headers=_hdr("alice"),
    )
    assert room.status_code == 200, room.text
    room_id = room.json()["id"]
    for name in ("bob", "carol", "dave"):
        join = await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": name, "role": "member"},
            headers=_hdr("alice"),
        )
        assert join.status_code == 200, join.text

    # bob and carol bid; dave does not.
    for participant, amount in (("bob", 0.4), ("carol", 0.6)):
        b = await client.post(
            "/v1/bid",
            json={
                "room_id": room_id,
                "message_id": "msg-fix8",
                "participant": participant,
                "bid": amount,
            },
            headers=_hdr(participant),
        )
        assert b.status_code == 200, b.text

    # dave tries to claim a window he never bid on. Must 403.
    rogue = await client.post(
        "/v1/claim",
        json={"room_id": room_id, "message_id": "msg-fix8"},
        headers=_hdr("dave"),
    )
    assert rogue.status_code == 403, rogue.text
    assert "bidder" in rogue.text or "not a bidder" in rogue.text

    # bob (a real bidder) can still claim it.
    legit = await client.post(
        "/v1/claim",
        json={"room_id": room_id, "message_id": "msg-fix8"},
        headers=_hdr("bob"),
    )
    assert legit.status_code == 200, legit.text


async def test_mention_bid_claim_flow_has_one_winner(client: AsyncClient):
    room_id = await _setup_room(client)

    triage = await client.post(
        "/v1/triage",
        json={
            "room_id": room_id,
            "message_id": "msg-e2e",
            "from_name": "alice",
            "content": "@bob @carol who can handle the deploy?",
        },
        headers=HEADERS,
    )
    assert triage.status_code == 200
    assert triage.json()["action"] == "RESPOND"
    assert triage.json()["candidates"] == ["bob", "carol"]

    bids = await asyncio.gather(
        client.post(
            "/v1/bid",
            json={
                "room_id": room_id,
                "message_id": "msg-e2e",
                "participant": "bob",
                "bid": 0.7,
            },
            headers=HEADERS,
        ),
        client.post(
            "/v1/bid",
            json={
                "room_id": room_id,
                "message_id": "msg-e2e",
                "participant": "carol",
                "bid": 0.9,
            },
            headers=HEADERS,
        ),
    )
    assert [resp.status_code for resp in bids] == [200, 200]

    claims = await asyncio.gather(
        client.post(
            "/v1/claim",
            json={"room_id": room_id, "message_id": "msg-e2e"},
            headers=HEADERS,
        ),
        client.post(
            "/v1/claim",
            json={"room_id": room_id, "message_id": "msg-e2e"},
            headers=HEADERS,
        ),
    )

    assert [resp.status_code for resp in claims] == [200, 200]
    claim_bodies = [resp.json() for resp in claims]
    assert {body["winner"] for body in claim_bodies} == {"carol"}
    assert len({body["claim_token"] for body in claim_bodies}) == 1
