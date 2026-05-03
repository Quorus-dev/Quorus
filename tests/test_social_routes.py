"""Integration tests for the Quorus Social Protocol v1 routes.

Mirrors the auth + transport pattern in ``tests/test_room_state.py``:
``Bearer test-secret`` legacy auth, ``ASGITransport(app=app)``, autouse
``_reset_state()`` fixture so each test starts clean.

Coverage:
* every verb returns 200 on the happy path
* blocking-disagree blocks new claims (409) until handoff or vote-majority
* defer cycle rejected (400)
* vote majority clears block
* double vote rejected (400)
* interrupt emits SSE
* GET state returns full matrix
* unknown verb rejected (400)
* missing auth rejected (401)
* blocking-disagree rate-limited after 12 within window
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app

HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def room_id(client: AsyncClient) -> str:
    """Create a room and add common members so the verb tests have somewhere
    to post."""
    resp = await client.post(
        "/rooms",
        json={"name": "social-test-room", "created_by": "alice"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    rid = resp.json()["id"]
    # Add bob + carol so multi-actor tests have valid members.
    for name in ("bob", "carol"):
        join = await client.post(
            f"/rooms/{rid}/join",
            json={"participant": name, "role": "member"},
            headers=HEADERS,
        )
        assert join.status_code == 200
    return rid


# ---------------------------------------------------------------------------
# Happy paths — verbs return 200 with envelope + state_change
# ---------------------------------------------------------------------------


class TestPostHappyPath:

    async def test_post_claim_returns_200_envelope(
        self, client: AsyncClient, room_id: str,
    ):
        resp = await client.post(
            "/v1/social/claim",
            json={
                "actor": "alice",
                "room_id": room_id,
                "payload": {
                    "task_id": "t-42", "eta_seconds": 600,
                    "scope": "ship social v1",
                },
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        env = body["envelope"]
        assert env["kind"] == "social"
        assert env["verb"] == "claim"
        assert env["actor"] == "alice"
        assert body["state_change"]["action"] == "claimed"
        assert body["state_change"]["task_id"] == "t-42"

    async def test_post_release_with_handoff_clears_block(
        self, client: AsyncClient, room_id: str,
    ):
        # First, alice claims
        await client.post(
            "/v1/social/claim",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {
                    "task_id": "t1", "eta_seconds": 300, "scope": "x",
                },
            },
            headers=HEADERS,
        )
        # Bob disagrees blocking
        d = await client.post(
            "/v1/social/disagree",
            json={
                "actor": "bob", "room_id": room_id,
                "payload": {
                    "ref_message_id": "msg-1",
                    "reason": "wrong approach",
                    "mode": "blocking",
                },
            },
            headers=HEADERS,
        )
        assert d.status_code == 200, d.text
        # State now blocked
        state = await client.get(
            f"/v1/social/state/{room_id}", headers=HEADERS,
        )
        assert state.json()["blocked_until_resolved"] is True

        # Alice releases with handoff to bob — clears block
        rel = await client.post(
            "/v1/social/release",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {
                    "task_id": "t1",
                    "reason": "handing off",
                    "handoff_to": "bob",
                },
            },
            headers=HEADERS,
        )
        assert rel.status_code == 200, rel.text
        state2 = await client.get(
            f"/v1/social/state/{room_id}", headers=HEADERS,
        )
        assert state2.json()["blocked_until_resolved"] is False

    async def test_post_defer_creates_graph_edge(
        self, client: AsyncClient, room_id: str,
    ):
        resp = await client.post(
            "/v1/social/defer",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {"to": "bob", "ttl_seconds": 60},
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        state = await client.get(
            f"/v1/social/state/{room_id}", headers=HEADERS,
        )
        defer = state.json()["defer_graph"]
        assert "alice" in defer
        assert "bob" in defer["alice"]

    async def test_post_interrupt_emits_sse(
        self, client: AsyncClient, room_id: str,
    ):
        # We simply verify the verb returns 200; SSE pushes are fired-and-forget
        # to local queues. End-to-end SSE coverage lives in test_sse.py.
        resp = await client.post(
            "/v1/social/interrupt",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {"ref_message_id": "msg-1", "reason": "prod down"},
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        env = resp.json()["envelope"]
        assert env["verb"] == "interrupt"


# ---------------------------------------------------------------------------
# State machine semantics
# ---------------------------------------------------------------------------


class TestStateMachine:

    async def test_post_disagree_blocking_blocks_new_claim_returns_409(
        self, client: AsyncClient, room_id: str,
    ):
        d = await client.post(
            "/v1/social/disagree",
            json={
                "actor": "bob", "room_id": room_id,
                "payload": {
                    "ref_message_id": "msg-1",
                    "reason": "wrong approach",
                    "mode": "blocking",
                },
            },
            headers=HEADERS,
        )
        assert d.status_code == 200

        # Now any new claim must 409
        c = await client.post(
            "/v1/social/claim",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {
                    "task_id": "t1", "eta_seconds": 60, "scope": "x",
                },
            },
            headers=HEADERS,
        )
        assert c.status_code == 409, c.text
        assert "room_blocked" in c.text

    async def test_post_defer_cycle_returns_400(
        self, client: AsyncClient, room_id: str,
    ):
        # alice → bob
        first = await client.post(
            "/v1/social/defer",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {"to": "bob", "ttl_seconds": 600},
            },
            headers=HEADERS,
        )
        assert first.status_code == 200
        # bob → alice closes a cycle
        cycle = await client.post(
            "/v1/social/defer",
            json={
                "actor": "bob", "room_id": room_id,
                "payload": {"to": "alice", "ttl_seconds": 600},
            },
            headers=HEADERS,
        )
        assert cycle.status_code == 400, cycle.text
        assert "defer_cycle" in cycle.text

    async def test_post_vote_majority_clears_block(
        self, client: AsyncClient, room_id: str,
    ):
        # bob blocks
        await client.post(
            "/v1/social/disagree",
            json={
                "actor": "bob", "room_id": room_id,
                "payload": {
                    "ref_message_id": "msg-1",
                    "reason": "wait",
                    "mode": "blocking",
                },
            },
            headers=HEADERS,
        )
        # alice + carol both vote "approve" with weight 1 each → 2/2 = 100%
        v1 = await client.post(
            "/v1/social/vote",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {"option": "approve"},
            },
            headers=HEADERS,
        )
        assert v1.status_code == 200, v1.text
        v2 = await client.post(
            "/v1/social/vote",
            json={
                "actor": "carol", "room_id": room_id,
                "payload": {"option": "approve"},
            },
            headers=HEADERS,
        )
        assert v2.status_code == 200, v2.text
        # Block cleared
        state = await client.get(
            f"/v1/social/state/{room_id}", headers=HEADERS,
        )
        assert state.json()["blocked_until_resolved"] is False

    async def test_post_vote_double_vote_returns_400(
        self, client: AsyncClient, room_id: str,
    ):
        v1 = await client.post(
            "/v1/social/vote",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {"poll_id": "p1", "option": "yes"},
            },
            headers=HEADERS,
        )
        assert v1.status_code == 200
        v2 = await client.post(
            "/v1/social/vote",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {"poll_id": "p1", "option": "no"},
            },
            headers=HEADERS,
        )
        assert v2.status_code == 400, v2.text
        assert "double_vote" in v2.text


# ---------------------------------------------------------------------------
# State endpoint
# ---------------------------------------------------------------------------


async def test_get_state_returns_full_matrix(
    client: AsyncClient, room_id: str,
):
    # Generate some state
    await client.post(
        "/v1/social/claim",
        json={
            "actor": "alice", "room_id": room_id,
            "payload": {"task_id": "t1", "eta_seconds": 60, "scope": "x"},
        },
        headers=HEADERS,
    )
    await client.post(
        "/v1/social/defer",
        json={
            "actor": "bob", "room_id": room_id,
            "payload": {"to": "carol", "ttl_seconds": 60},
        },
        headers=HEADERS,
    )
    state = await client.get(
        f"/v1/social/state/{room_id}", headers=HEADERS,
    )
    assert state.status_code == 200
    body = state.json()
    expected_keys = {
        "room_id", "blocked_until_resolved", "blocking_disagree_ref",
        "defer_graph", "votes", "voters", "claims", "queue", "social_credit",
    }
    assert expected_keys.issubset(body.keys())
    assert "t1" in body["claims"]
    assert "alice" in body["claims"]["t1"]["actor"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_unknown_verb_returns_400(
    client: AsyncClient, room_id: str,
):
    resp = await client.post(
        "/v1/social/fly",
        json={
            "actor": "alice", "room_id": room_id, "payload": {},
        },
        headers=HEADERS,
    )
    assert resp.status_code == 400, resp.text


async def test_unauthorized_returns_401(
    client: AsyncClient, room_id: str,
):
    resp = await client.post(
        "/v1/social/claim",
        json={
            "actor": "alice", "room_id": room_id,
            "payload": {"task_id": "t1", "eta_seconds": 60, "scope": "x"},
        },
        # No HEADERS — missing Authorization
    )
    assert resp.status_code == 401, resp.text


async def test_blocking_disagree_rate_limited_after_12(
    client: AsyncClient, room_id: str,
):
    # The limit is 12 per 5min window — fire 12 then expect 429 on the 13th.
    payload = {
        "actor": "alice", "room_id": room_id,
        "payload": {
            "ref_message_id": "m1",
            "reason": "no",
            "mode": "blocking",
        },
    }
    accepted = 0
    for _ in range(12):
        r = await client.post(
            "/v1/social/disagree", json=payload, headers=HEADERS,
        )
        if r.status_code == 200:
            accepted += 1
    assert accepted == 12, f"expected 12 accepts, got {accepted}"
    # 13th must 429
    r = await client.post(
        "/v1/social/disagree", json=payload, headers=HEADERS,
    )
    assert r.status_code == 429, r.text
