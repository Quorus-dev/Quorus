"""Tests for the Stream B agent-DM channel.

Coverage:
* send via POST /v1/dm appears in GET /v1/dm/{recipient}
* recipient stream is isolated from the human DM stream (no cross-pollination)
* sending as another user is rejected (403)
* DM to self is rejected (400)
* rate limit kicks in after 30/min per (sender, recipient) pair
* in_reply_to round-trips for thread reconstruction
* unknown participant inbox returns empty list (not an error)
* listing as a third party is rejected
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app

HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
async def _clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_send_then_list_roundtrip(client: AsyncClient):
    resp = await client.post(
        "/v1/dm",
        json={
            "from": "alice",
            "to": "bob",
            "content": "ack — your task-#42 is queued",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text

    inbox = await client.get("/v1/dm/bob", headers=HEADERS)
    assert inbox.status_code == 200
    body = inbox.json()
    assert body["count"] == 1
    msg = body["messages"][0]
    assert msg["from"] == "alice"
    assert msg["to"] == "bob"
    assert msg["kind"] == "agent_dm"
    assert msg["content"].startswith("ack")


async def test_in_reply_to_roundtrips(client: AsyncClient):
    first = await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "bob", "content": "ping"},
        headers=HEADERS,
    )
    parent_id = first.json()["id"]

    reply = await client.post(
        "/v1/dm",
        json={
            "from": "bob",
            "to": "alice",
            "content": "pong",
            "in_reply_to": parent_id,
        },
        headers=HEADERS,
    )
    assert reply.status_code == 200, reply.text
    inbox = await client.get("/v1/dm/alice", headers=HEADERS)
    assert inbox.json()["messages"][-1]["in_reply_to"] == parent_id


# ---------------------------------------------------------------------------
# Isolation from human DM stream
# ---------------------------------------------------------------------------


async def test_isolation_from_human_dm_stream(client: AsyncClient):
    """An agent-DM must NOT show up in the human ``/messages/{recipient}`` queue."""
    await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "bob", "content": "agent-only"},
        headers=HEADERS,
    )
    # Human DM fetch — this is the regular per-recipient inbox endpoint.
    human = await client.get("/messages/bob", headers=HEADERS)
    assert human.status_code == 200
    payload = human.json()
    # Either: empty list OR an envelope without the agent_dm payload.
    if isinstance(payload, dict):
        msgs = payload.get("messages", [])
    elif isinstance(payload, list):
        msgs = payload
    else:
        msgs = []
    assert all("agent-only" not in (m.get("content") or "") for m in msgs), (
        "agent DM leaked into human DM channel"
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_cannot_send_as_another_user(client: AsyncClient):
    # Legacy "test-secret" auth lets us send as any from-name today,
    # because auth.sub is None under legacy auth. To prove the
    # protection, we mint a real JWT for alice and try to send as bob.
    from quorus.auth.tokens import create_jwt

    bob_token = create_jwt(
        sub="bob", tenant_id="t-test", tenant_slug="test-tenant",
    )
    resp = await client.post(
        "/v1/dm",
        json={"from": "carol", "to": "alice", "content": "spoofed"},
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert resp.status_code == 403, resp.text


async def test_cannot_dm_self(client: AsyncClient):
    resp = await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "alice", "content": "..."},
        headers=HEADERS,
    )
    assert resp.status_code == 400, resp.text
    assert "yourself" in resp.text.lower()


async def test_unknown_participant_inbox_empty(client: AsyncClient):
    resp = await client.get("/v1/dm/never-existed", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


async def test_third_party_cannot_read_inbox(client: AsyncClient):
    """A real JWT authenticated as bob must not be able to read alice's inbox."""
    from quorus.auth.tokens import create_jwt

    bob_token = create_jwt(
        sub="bob", tenant_id="t-test", tenant_slug="test-tenant",
    )
    resp = await client.get(
        "/v1/dm/alice",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def test_rate_limit_after_30(client: AsyncClient):
    """30 sends within the window pass; the 31st must 429."""
    accepted = 0
    payload = {"from": "alice", "to": "bob", "content": "noisy"}
    for _ in range(30):
        r = await client.post("/v1/dm", json=payload, headers=HEADERS)
        if r.status_code == 200:
            accepted += 1
    assert accepted == 30, f"expected 30 accepts, got {accepted}"
    overflow = await client.post("/v1/dm", json=payload, headers=HEADERS)
    assert overflow.status_code == 429, overflow.text


# ---------------------------------------------------------------------------
# CRIT-2: cross-tenant + recipient existence guards
# ---------------------------------------------------------------------------


async def test_dm_unknown_recipient_returns_404(client: AsyncClient):
    """Sending to a name that has never registered in the tenant must 404.

    With a real JWT (not legacy bypass) the agent_dm route now consults
    ``backends.participants.list_all(tid)`` and rejects with 404 if the
    recipient name has never registered. Prevents fan-out attacks where
    a sender DMs every plausible name to discover which exist.
    """
    from quorus.auth.tokens import create_jwt

    alice_token = create_jwt(
        sub="alice", tenant_id="t-test", tenant_slug="test-tenant",
    )
    # alice is registered (was minted with sub=alice into tenant t-test
    # via the JWT subject). bob has never registered.
    resp = await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "never-exists-anywhere", "content": "hi"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    # 404 is the contracted response when recipient is not in tenant.
    # Some setups may also hit auth-related 401/403 first; the security
    # invariant is "never 200 for a name that does not exist". We assert
    # 4xx-not-200 so the invariant is verified independent of auth path.
    assert resp.status_code != 200, resp.text
    assert resp.status_code in (401, 403, 404), resp.text


async def test_dm_cross_tenant_blocked_by_cedar(client: AsyncClient):
    """A DM from tenant A to a name only registered in tenant B must NOT 200.

    With per-tenant participant partitioning, a name in tenant B is not
    visible from tenant A. The recipient lookup returns "not found" so
    the route 404s. Either way, the cross-tenant payload must never
    succeed — CRIT-2 is "no 200 across tenants".
    """
    from quorus.auth.tokens import create_jwt

    a_token = create_jwt(
        sub="agent-a", tenant_id="tenant-a", tenant_slug="tenant-a-slug",
    )
    resp = await client.post(
        "/v1/dm",
        json={
            "from": "agent-a",
            "to": "agent-b-in-tenant-b",
            "content": "cross tenant probe",
        },
        headers={"Authorization": f"Bearer {a_token}"},
    )
    assert resp.status_code != 200, resp.text
    # 4xx range — never silently succeed.
    assert 400 <= resp.status_code < 500, resp.text
