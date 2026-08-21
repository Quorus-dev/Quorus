"""R2 (WAKE_REBUILD_SPEC): GET /rooms/{id} surfaces per-member presence +
queued message depth so clients can render "● active" / "○ away — N queued"
instead of a silent void when a mentioned agent's host is asleep."""

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


async def _make_room(client: AsyncClient, members: list[str]) -> str:
    resp = await client.post(
        "/rooms",
        json={"name": "presence-room", "created_by": members[0]},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    room_id = resp.json()["id"]
    for m in members[1:]:
        resp = await client.post(
            f"/rooms/{room_id}/join", json={"participant": m}, headers=HEADERS
        )
        assert resp.status_code == 200, resp.text
    return room_id


async def test_members_default_to_away_with_zero_queue(client: AsyncClient):
    room_id = await _make_room(client, ["alice", "bob-claude"])
    resp = await client.get(f"/rooms/{room_id}", headers=HEADERS)
    assert resp.status_code == 200
    presence = resp.json()["member_presence"]
    assert presence["alice"] == {"presence": "away", "queued": 0}
    assert presence["bob-claude"] == {"presence": "away", "queued": 0}


async def test_heartbeat_flips_member_to_active(client: AsyncClient):
    room_id = await _make_room(client, ["alice", "bob-claude"])
    resp = await client.post(
        "/heartbeat",
        json={"instance_name": "bob-claude", "status": "active", "room": ""},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"/rooms/{room_id}", headers=HEADERS)
    presence = resp.json()["member_presence"]
    assert presence["bob-claude"]["presence"] == "active"
    assert presence["alice"]["presence"] == "away"


async def test_queued_counts_undelivered_room_fanout(client: AsyncClient):
    """A room message fans out into each member's durable inbox; members who
    haven't fetched yet show a non-zero queue depth."""
    room_id = await _make_room(client, ["alice", "bob-claude"])
    resp = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "@bob-claude are you there?"},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"/rooms/{room_id}", headers=HEADERS)
    presence = resp.json()["member_presence"]
    assert presence["bob-claude"]["queued"] >= 1

    # Draining the inbox (fetch + ack) brings the count back to zero.
    resp = await client.get(
        "/messages/bob-claude", params={"ack": "server"}, headers=HEADERS
    )
    assert resp.status_code == 200
    resp = await client.get(f"/rooms/{room_id}", headers=HEADERS)
    assert resp.json()["member_presence"]["bob-claude"]["queued"] == 0


async def test_server_ack_actually_clears_pending(client: AsyncClient):
    """Regression: InMemoryMessageBackend.ack only understood its internal
    uuid token, but MessageService rebuilds ack_token as a JSON id list
    (the Redis contract) — so every token ACK was a silent no-op and acked
    messages were redelivered after each visibility timeout."""
    room_id = await _make_room(client, ["alice", "bob-claude"])
    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "hello bob"},
        headers=HEADERS,
    )
    # Manual-ack fetch, then ack by token — the reflexd drain path.
    resp = await client.get(
        "/messages/bob-claude", params={"ack": "manual"}, headers=HEADERS
    )
    body = resp.json()
    assert body["messages"] and body["ack_token"]
    resp = await client.post(
        "/messages/bob-claude/ack",
        json={"ack_token": body["ack_token"]},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    resp = await client.get("/messages/bob-claude/peek", headers=HEADERS)
    assert resp.json() == {"count": 0, "pending": 0, "recipient": "bob-claude"}
