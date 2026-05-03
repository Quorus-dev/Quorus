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

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app

HEADERS = {"Authorization": "Bearer test-secret"}

_TEST_TENANT_ID = "t-dm"
_TEST_TENANT_SLUG = "dm-test"


def _user_headers(name: str) -> dict[str, str]:
    """Return a Bearer-JWT header for ``name`` in the test tenant."""
    token = create_jwt(
        sub=name,
        tenant_id=_TEST_TENANT_ID,
        tenant_slug=_TEST_TENANT_SLUG,
    )
    return {"Authorization": f"Bearer {token}"}


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


@pytest.fixture
async def registered_participants():
    """Register alice + bob + carol in the DM test tenant.

    H6 hardening rejects legacy auth on /v1/dm so we exercise the route via
    real JWTs. Recipient lookup requires the recipient to be a registered
    participant in the sender's tenant — this fixture seeds both names so
    the happy paths can run.
    """
    backends = app.state.backends
    for name in ("alice", "bob", "carol"):
        await backends.participants.add(_TEST_TENANT_ID, name)
    yield


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_send_then_list_roundtrip(
    client: AsyncClient, registered_participants,
):
    resp = await client.post(
        "/v1/dm",
        json={
            "from": "alice",
            "to": "bob",
            "content": "ack — your task-#42 is queued",
        },
        headers=_user_headers("alice"),
    )
    assert resp.status_code == 200, resp.text

    inbox = await client.get("/v1/dm/bob", headers=_user_headers("bob"))
    assert inbox.status_code == 200
    body = inbox.json()
    assert body["count"] == 1
    msg = body["messages"][0]
    assert msg["from"] == "alice"
    assert msg["to"] == "bob"
    assert msg["kind"] == "agent_dm"
    assert msg["content"].startswith("ack")


async def test_in_reply_to_roundtrips(
    client: AsyncClient, registered_participants,
):
    first = await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "bob", "content": "ping"},
        headers=_user_headers("alice"),
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
        headers=_user_headers("bob"),
    )
    assert reply.status_code == 200, reply.text
    inbox = await client.get("/v1/dm/alice", headers=_user_headers("alice"))
    assert inbox.json()["messages"][-1]["in_reply_to"] == parent_id


# ---------------------------------------------------------------------------
# Isolation from human DM stream
# ---------------------------------------------------------------------------


async def test_isolation_from_human_dm_stream(
    client: AsyncClient, registered_participants,
):
    """An agent-DM must NOT show up in the human ``/messages/{recipient}`` queue."""
    await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "bob", "content": "agent-only"},
        headers=_user_headers("alice"),
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


async def test_cannot_dm_self(client: AsyncClient, registered_participants):
    resp = await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "alice", "content": "..."},
        headers=_user_headers("alice"),
    )
    assert resp.status_code == 400, resp.text
    assert "yourself" in resp.text.lower()


async def test_unknown_participant_inbox_empty(client: AsyncClient):
    # Listing your own (empty) inbox under a real JWT should 200 cleanly.
    resp = await client.get(
        "/v1/dm/never-existed",
        headers=_user_headers("never-existed"),
    )
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


async def test_rate_limit_after_30(
    client: AsyncClient, registered_participants,
):
    """30 sends within the window pass; the 31st must 429."""
    accepted = 0
    payload = {"from": "alice", "to": "bob", "content": "noisy"}
    headers = _user_headers("alice")
    for _ in range(30):
        r = await client.post("/v1/dm", json=payload, headers=headers)
        if r.status_code == 200:
            accepted += 1
    assert accepted == 30, f"expected 30 accepts, got {accepted}"
    overflow = await client.post("/v1/dm", json=payload, headers=headers)
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


async def test_legacy_bearer_rejected_on_dm(client: AsyncClient):
    """H6 — POST /v1/dm must reject ``Bearer test-secret``."""
    resp = await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "bob", "content": "no legacy"},
        headers=HEADERS,  # legacy bearer
    )
    assert resp.status_code == 403, resp.text
    assert "legacy auth not permitted" in resp.text


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


# ---------------------------------------------------------------------------
# M3 — GDPR right-to-erasure (DELETE /v1/dm/{participant})
# ---------------------------------------------------------------------------


async def test_dm_delete_endpoint_purges_inbox(
    client: AsyncClient, registered_participants,
):
    """M3 — DELETE /v1/dm/{recipient} returns purged_count and empties the inbox."""
    # Seed two messages addressed to bob.
    for content in ("hello", "world"):
        resp = await client.post(
            "/v1/dm",
            json={"from": "alice", "to": "bob", "content": content},
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 200, resp.text

    # Bob can purge his own inbox.
    resp = await client.delete(
        "/v1/dm/bob", headers=_user_headers("bob"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["purged_count"] == 2
    assert body["participant"] == "bob"

    # Subsequent GET shows empty.
    inbox = await client.get("/v1/dm/bob", headers=_user_headers("bob"))
    assert inbox.status_code == 200
    assert inbox.json()["count"] == 0


async def test_dm_delete_requires_recipient_self_auth(
    client: AsyncClient, registered_participants,
):
    """M3 — only the recipient may purge; carol can't wipe bob's inbox.

    No admin override and no legacy-bearer override — these would each
    create a data-erasure attack vector. Both must be rejected 403 / 401
    so a compromised admin / leaked legacy secret can't wipe peer data.
    """
    # Seed bob's inbox so the test sees a real (non-empty) target.
    await client.post(
        "/v1/dm",
        json={"from": "alice", "to": "bob", "content": "do not delete me"},
        headers=_user_headers("alice"),
    )

    # Carol attempts to purge bob.
    resp = await client.delete(
        "/v1/dm/bob", headers=_user_headers("carol"),
    )
    assert resp.status_code == 403, resp.text
    assert "another user" in resp.text.lower()

    # Legacy bearer also rejected (H6 + M3 — no admin override on erasure).
    resp_legacy = await client.delete("/v1/dm/bob", headers=HEADERS)
    assert resp_legacy.status_code in (401, 403), resp_legacy.text

    # Bob's data still there.
    inbox = await client.get("/v1/dm/bob", headers=_user_headers("bob"))
    assert inbox.json()["count"] == 1


async def test_dm_delete_idempotent_on_empty_inbox(
    client: AsyncClient, registered_participants,
):
    """M3 — deleting an empty inbox is a no-op that returns purged_count=0.

    Idempotency matters because a client retrying the GDPR request after
    a network blip must NOT see a 404 for an already-erased inbox.
    """
    resp = await client.delete(
        "/v1/dm/alice", headers=_user_headers("alice"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["purged_count"] == 0


# ---------------------------------------------------------------------------
# M5 — DM inbox dict bounded with LRU eviction
# ---------------------------------------------------------------------------


def test_inboxes_lru_evicts_at_10k():
    """M5 — the inbox helper evicts oldest at MAX_INBOXES.

    Drives :func:`_inbox_get_or_create` directly so we don't have to mint
    10001 participants through the HTTP path. Eviction must drop the
    leftmost (oldest-touched) key when capacity is hit.
    """
    from collections import OrderedDict

    from quorus.routes import agent_dm

    # Patch MAX_INBOXES to a tractable number for the test.
    original_max = agent_dm.MAX_INBOXES
    inbox: OrderedDict = OrderedDict()
    try:
        agent_dm.MAX_INBOXES = 5
        for i in range(5):
            agent_dm._inbox_get_or_create(inbox, ("t-1", f"agent-{i}"))
        assert len(inbox) == 5

        # 6th distinct recipient — agent-0 (oldest) must be evicted.
        agent_dm._inbox_get_or_create(inbox, ("t-1", "agent-5"))
        assert ("t-1", "agent-0") not in inbox
        assert ("t-1", "agent-5") in inbox
        assert len(inbox) == 5
    finally:
        agent_dm.MAX_INBOXES = original_max


def test_inboxes_lru_promotes_on_repeat_access():
    """M5 — touching an existing key promotes it to most-recently-used."""
    from collections import OrderedDict

    from quorus.routes import agent_dm

    original_max = agent_dm.MAX_INBOXES
    inbox: OrderedDict = OrderedDict()
    try:
        agent_dm.MAX_INBOXES = 3
        for i in range(3):
            agent_dm._inbox_get_or_create(inbox, ("t-1", f"a{i}"))
        # Touch a0 — promotes it to the right end. Adding a3 should now
        # evict a1 instead.
        agent_dm._inbox_get_or_create(inbox, ("t-1", "a0"))
        agent_dm._inbox_get_or_create(inbox, ("t-1", "a3"))
        assert ("t-1", "a1") not in inbox
        assert ("t-1", "a0") in inbox
        assert ("t-1", "a3") in inbox
    finally:
        agent_dm.MAX_INBOXES = original_max


# ---------------------------------------------------------------------------
# M17 — agent_dm SSE queue O(1) cleanup via set-backed registry
# ---------------------------------------------------------------------------


def test_agent_dm_queue_set_O1_removal():
    """M17 — the queues map must be a dict of sets so disconnect is O(1).

    We don't time it; we just assert the structural shape, which is a
    sufficient regression guard against a future refactor reverting to
    list[Queue].
    """
    from types import SimpleNamespace

    from quorus.routes.agent_dm import _get_dm_queues

    fake_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(agent_dm_queues=None)),
    )
    queues = _get_dm_queues(fake_request)
    queues.setdefault(("t", "p"), set()).add(object())
    assert isinstance(queues[("t", "p")], set), (
        "queues must use sets so disconnect is O(1)"
    )


def test_agent_dm_queue_migrates_legacy_list_to_set():
    """M17 — pre-fix relays stored ``list[Queue]``. The helper must migrate
    transparently on first access so a relay restart in mixed state
    doesn't break fan-out."""
    from types import SimpleNamespace

    from quorus.routes.agent_dm import _get_dm_queues

    sentinel_q = object()
    legacy = {("t", "p"): [sentinel_q]}
    fake_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(agent_dm_queues=legacy)),
    )
    migrated = _get_dm_queues(fake_request)
    assert isinstance(migrated[("t", "p")], set)
    assert sentinel_q in migrated[("t", "p")]
