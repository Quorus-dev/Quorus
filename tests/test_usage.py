"""Tests for /v1/usage endpoints — per-tenant usage statistics.

GET /v1/usage                 — tenant-level totals, per-room breakdown, top senders
GET /v1/usage/rooms/{room_id} — single room breakdown with locked files, goal, agents
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from murmur.relay import _reset_state, app

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


# ---------------------------------------------------------------------------
# GET /v1/usage — tenant-level aggregate stats
# ---------------------------------------------------------------------------


async def test_get_usage_returns_expected_schema(client: AsyncClient):
    """GET /v1/usage returns a dict with the required top-level keys."""
    resp = await client.get("/v1/usage", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    required_keys = {"tenant_id", "snapshot_at", "totals", "rooms", "top_senders"}
    assert required_keys.issubset(data.keys()), (
        f"Missing keys: {required_keys - data.keys()}"
    )

    totals = data["totals"]
    assert isinstance(totals["messages_sent"], int)
    assert isinstance(totals["messages_delivered"], int)
    assert isinstance(totals["active_rooms"], int)
    assert isinstance(totals["active_agents"], int)
    assert isinstance(data["rooms"], list)
    assert isinstance(data["top_senders"], list)


async def test_get_usage_no_auth_returns_401(client: AsyncClient):
    """GET /v1/usage without auth returns 401."""
    resp = await client.get("/v1/usage")
    assert resp.status_code == 401


async def test_usage_room_breakdown(client: AsyncClient):
    """After creating a room and sending a message, usage rooms list reflects it."""
    room_resp = await client.post(
        "/rooms",
        json={"name": "usage-test-room", "created_by": "agent-one"},
        headers=HEADERS,
    )
    assert room_resp.status_code == 200
    room_id = room_resp.json()["id"]

    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "agent-two"},
        headers=HEADERS,
    )
    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "agent-one", "content": "hello usage"},
        headers=HEADERS,
    )

    resp = await client.get("/v1/usage", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    room_ids = [r["room_id"] for r in data["rooms"]]
    assert room_id in room_ids

    room_data = next(r for r in data["rooms"] if r["room_id"] == room_id)
    assert room_data["message_count"] >= 1
    assert "room_name" in room_data
    assert isinstance(room_data["active_agents"], int)
    assert isinstance(room_data["locked_files"], int)


async def test_usage_top_senders(client: AsyncClient):
    """Sending messages populates top_senders."""
    room_resp = await client.post(
        "/rooms",
        json={"name": "top-sender-room", "created_by": "sender-bot"},
        headers=HEADERS,
    )
    room_id = room_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "receiver-bot"},
        headers=HEADERS,
    )
    for _ in range(3):
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "sender-bot", "content": "stat me"},
            headers=HEADERS,
        )

    resp = await client.get("/v1/usage", headers=HEADERS)
    data = resp.json()
    sender_names = [s["name"] for s in data["top_senders"]]
    assert "sender-bot" in sender_names


async def test_usage_rooms_sorted_by_activity(client: AsyncClient):
    """Rooms list is sorted by message_count descending."""
    for room_name in ("busy-room", "quiet-room"):
        room_resp = await client.post(
            "/rooms",
            json={"name": room_name, "created_by": "agent"},
            headers=HEADERS,
        )
        room_id = room_resp.json()["id"]
        await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": "observer"},
            headers=HEADERS,
        )

    resp = await client.get("/v1/usage", headers=HEADERS)
    rooms = resp.json()["rooms"]
    counts = [r["message_count"] for r in rooms]
    assert counts == sorted(counts, reverse=True), (
        "Rooms should be sorted by message_count descending"
    )


async def test_usage_totals_active_rooms_count(client: AsyncClient):
    """totals.active_rooms matches the number of rooms created."""
    for i in range(3):
        await client.post(
            "/rooms",
            json={"name": f"count-room-{i}", "created_by": "alice"},
            headers=HEADERS,
        )
    resp = await client.get("/v1/usage", headers=HEADERS)
    assert resp.json()["totals"]["active_rooms"] == 3


# ---------------------------------------------------------------------------
# GET /v1/usage/rooms/{room_id} — single room breakdown
# ---------------------------------------------------------------------------


async def test_room_usage_returns_expected_schema(client: AsyncClient):
    """GET /v1/usage/rooms/{id} returns the expected schema."""
    room = await client.post(
        "/rooms",
        json={"name": "room-usage-detail", "created_by": "alice"},
        headers=HEADERS,
    )
    rid = room.json()["id"]
    resp = await client.get(f"/v1/usage/rooms/{rid}", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    required_keys = {
        "room_id", "room_name", "snapshot_at", "message_count",
        "active_agents", "locked_files", "active_goal", "top_senders",
    }
    assert required_keys.issubset(data.keys()), (
        f"Missing keys: {required_keys - data.keys()}"
    )
    assert data["room_id"] == rid
    assert isinstance(data["active_agents"], list)
    assert isinstance(data["locked_files"], dict)
    assert isinstance(data["top_senders"], list)


async def test_room_usage_not_found(client: AsyncClient):
    """GET /v1/usage/rooms/unknown returns 404."""
    resp = await client.get("/v1/usage/rooms/no-such-room", headers=HEADERS)
    assert resp.status_code == 404


async def test_room_usage_no_auth_returns_401(client: AsyncClient):
    """GET /v1/usage/rooms/{id} without auth returns 401."""
    resp = await client.get("/v1/usage/rooms/any-room")
    assert resp.status_code == 401


async def test_room_usage_shows_locked_files(client: AsyncClient):
    """Locked files appear in the room usage endpoint."""
    room = await client.post(
        "/rooms",
        json={"name": "room-lock-usage", "created_by": "alice"},
        headers=HEADERS,
    )
    rid = room.json()["id"]
    await client.post(
        f"/rooms/{rid}/lock",
        json={"file_path": "src/main.py", "claimed_by": "alice", "ttl_seconds": 300},
        headers=HEADERS,
    )
    resp = await client.get(f"/v1/usage/rooms/{rid}", headers=HEADERS)
    data = resp.json()
    assert "src/main.py" in data["locked_files"]
    assert data["locked_files"]["src/main.py"]["held_by"] == "alice"


async def test_room_usage_shows_goal(client: AsyncClient):
    """Active goal appears in the room usage endpoint."""
    room = await client.post(
        "/rooms",
        json={"name": "room-goal-usage", "created_by": "alice"},
        headers=HEADERS,
    )
    rid = room.json()["id"]
    await client.patch(
        f"/rooms/{rid}/state/goal",
        json={"goal": "Ship by Friday"},
        headers=HEADERS,
    )
    resp = await client.get(f"/v1/usage/rooms/{rid}", headers=HEADERS)
    assert resp.json()["active_goal"] == "Ship by Friday"


async def test_room_usage_top_senders(client: AsyncClient):
    """top_senders in room usage are derived from message history."""
    room = await client.post(
        "/rooms",
        json={"name": "room-senders-usage", "created_by": "alice"},
        headers=HEADERS,
    )
    rid = room.json()["id"]
    await client.post(
        f"/rooms/{rid}/join",
        json={"participant": "bob"},
        headers=HEADERS,
    )
    for _ in range(5):
        await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "alice", "content": "message"},
            headers=HEADERS,
        )
    for _ in range(2):
        await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "bob", "content": "reply"},
            headers=HEADERS,
        )

    resp = await client.get(f"/v1/usage/rooms/{rid}", headers=HEADERS)
    senders = resp.json()["top_senders"]
    # alice should be first (5 messages > bob's 2)
    assert senders[0]["name"] == "alice"
    assert senders[0]["count"] == 5
    assert senders[1]["name"] == "bob"
    assert senders[1]["count"] == 2


async def test_room_usage_resolves_by_name(client: AsyncClient):
    """GET /v1/usage/rooms/{name} resolves by room name."""
    room = await client.post(
        "/rooms",
        json={"name": "name-resolve-room", "created_by": "alice"},
        headers=HEADERS,
    )
    rid = room.json()["id"]
    resp = await client.get("/v1/usage/rooms/name-resolve-room", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["room_id"] == rid


# ---------------------------------------------------------------------------
# Smoke test — verifies the router is importable
# ---------------------------------------------------------------------------


async def test_usage_router_is_importable():
    """The usage router module is importable and has the expected routes."""
    from murmur.routes.usage import router

    paths = [r.path for r in router.routes]  # type: ignore[attr-defined]
    assert "/v1/usage" in paths, f"Expected /v1/usage in router paths, got: {paths}"
    assert "/v1/usage/rooms/{room_id}" in paths, (
        f"Expected /v1/usage/rooms/{{room_id}} in router paths, got: {paths}"
    )
