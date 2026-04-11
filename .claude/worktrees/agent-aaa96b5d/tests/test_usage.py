"""Tests for GET /v1/usage and GET /v1/usage/rooms/{room_id}.

Covers:
  - Schema correctness (required keys and types)
  - Auth (401 on missing token)
  - Data accuracy (room breakdown, top senders, message counts)
  - Single-room breakdown endpoint
  - Sorting contract (rooms ordered by message_count descending)
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
# Smoke: module and route registration
# ---------------------------------------------------------------------------


async def test_usage_router_is_importable():
    """The usage router module is importable and has the expected routes."""
    from murmur.routes.usage import router

    paths = [r.path for r in router.routes]  # type: ignore[attr-defined]
    assert "/usage" in paths, f"Expected /usage in router paths, got: {paths}"


async def test_usage_room_router_is_importable():
    """The per-room usage route exists on the usage router."""
    from murmur.routes.usage import router

    paths = [r.path for r in router.routes]  # type: ignore[attr-defined]
    assert "/usage/rooms/{room_id}" in paths, (
        f"Expected /usage/rooms/{{room_id}} in router paths, got: {paths}"
    )


# ---------------------------------------------------------------------------
# GET /v1/usage — schema and auth
# ---------------------------------------------------------------------------


async def test_get_usage_requires_auth(client: AsyncClient):
    """GET /v1/usage returns 401 when no Authorization header is provided."""
    resp = await client.get("/v1/usage")
    assert resp.status_code == 401


async def test_get_usage_returns_200(client: AsyncClient):
    """GET /v1/usage returns 200 with valid auth."""
    resp = await client.get("/v1/usage", headers=HEADERS)
    assert resp.status_code == 200


async def test_get_usage_schema(client: AsyncClient):
    """GET /v1/usage returns a response with all required top-level keys."""
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


async def test_get_usage_totals_empty_relay(client: AsyncClient):
    """With no rooms, usage totals should all be zero."""
    resp = await client.get("/v1/usage", headers=HEADERS)
    data = resp.json()

    totals = data["totals"]
    assert totals["messages_sent"] == 0
    assert totals["active_rooms"] == 0
    assert data["rooms"] == []
    assert data["top_senders"] == []


# ---------------------------------------------------------------------------
# GET /v1/usage — data accuracy after creating rooms and sending messages
# ---------------------------------------------------------------------------


async def _create_room_and_send(
    client: AsyncClient,
    room_name: str,
    creator: str,
    sender: str,
    n_messages: int,
) -> str:
    """Helper: create room, add a second member, send N messages. Returns room id."""
    room_resp = await client.post(
        "/rooms",
        json={"name": room_name, "created_by": creator},
        headers=HEADERS,
    )
    assert room_resp.status_code == 200
    room_id = room_resp.json()["id"]

    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": sender},
        headers=HEADERS,
    )
    for i in range(n_messages):
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": sender, "content": f"message {i}"},
            headers=HEADERS,
        )
    return room_id


async def test_usage_room_appears_in_breakdown(client: AsyncClient):
    """After creating a room, it should appear in the rooms list."""
    room_id = await _create_room_and_send(client, "usage-room", "alpha", "beta", 1)

    resp = await client.get("/v1/usage", headers=HEADERS)
    data = resp.json()

    room_ids = [r["room_id"] for r in data["rooms"]]
    assert room_id in room_ids


async def test_usage_room_message_count(client: AsyncClient):
    """Room message_count in usage breakdown reflects messages sent."""
    room_id = await _create_room_and_send(client, "count-room", "alpha", "beta", 3)

    resp = await client.get("/v1/usage", headers=HEADERS)
    data = resp.json()

    room_data = next(r for r in data["rooms"] if r["room_id"] == room_id)
    assert room_data["message_count"] >= 3


async def test_usage_room_has_required_fields(client: AsyncClient):
    """Each room entry in the breakdown has all expected fields."""
    room_id = await _create_room_and_send(client, "schema-room", "alpha", "beta", 1)

    resp = await client.get("/v1/usage", headers=HEADERS)
    data = resp.json()

    room_data = next(r for r in data["rooms"] if r["room_id"] == room_id)
    required = {"room_id", "room_name", "message_count", "active_agents", "locked_files"}
    assert required.issubset(room_data.keys()), (
        f"Missing room fields: {required - room_data.keys()}"
    )
    assert isinstance(room_data["message_count"], int)
    assert isinstance(room_data["active_agents"], int)
    assert isinstance(room_data["locked_files"], int)


async def test_usage_top_senders_populated(client: AsyncClient):
    """Sending messages populates the top_senders list."""
    await _create_room_and_send(client, "sender-room", "alpha", "prolific-sender", 2)

    resp = await client.get("/v1/usage", headers=HEADERS)
    data = resp.json()

    sender_names = [s["name"] for s in data["top_senders"]]
    assert "prolific-sender" in sender_names


async def test_usage_top_senders_count_accurate(client: AsyncClient):
    """Top sender count matches messages actually sent."""
    await _create_room_and_send(client, "count-sender-room", "creator", "counter", 4)

    resp = await client.get("/v1/usage", headers=HEADERS)
    data = resp.json()

    counter_entry = next(
        (s for s in data["top_senders"] if s["name"] == "counter"), None
    )
    assert counter_entry is not None
    assert counter_entry["count"] >= 4


async def test_usage_rooms_sorted_by_message_count(client: AsyncClient):
    """Rooms list is sorted by message_count descending."""
    # Create busy room (3 messages) then quiet room (1 message)
    await _create_room_and_send(client, "busy-room", "alpha", "sender-a", 3)
    await _create_room_and_send(client, "quiet-room", "alpha", "sender-b", 1)

    resp = await client.get("/v1/usage", headers=HEADERS)
    counts = [r["message_count"] for r in resp.json()["rooms"]]
    assert counts == sorted(counts, reverse=True), (
        "Rooms must be sorted by message_count descending"
    )


async def test_usage_active_rooms_count(client: AsyncClient):
    """totals.active_rooms reflects the number of rooms created."""
    for i in range(3):
        await client.post(
            "/rooms",
            json={"name": f"room-{i}", "created_by": "agent"},
            headers=HEADERS,
        )

    resp = await client.get("/v1/usage", headers=HEADERS)
    assert resp.json()["totals"]["active_rooms"] == 3


# ---------------------------------------------------------------------------
# GET /v1/usage/rooms/{room_id} — per-room breakdown
# ---------------------------------------------------------------------------


async def test_get_room_usage_requires_auth(client: AsyncClient):
    """GET /v1/usage/rooms/{room_id} returns 401 without auth."""
    room_resp = await client.post(
        "/rooms",
        json={"name": "auth-test-room", "created_by": "agent"},
        headers=HEADERS,
    )
    room_id = room_resp.json()["id"]

    resp = await client.get(f"/v1/usage/rooms/{room_id}")
    assert resp.status_code == 401


async def test_get_room_usage_unknown_room_404(client: AsyncClient):
    """GET /v1/usage/rooms/{room_id} returns 404 for non-existent room."""
    resp = await client.get("/v1/usage/rooms/no-such-room-xyz", headers=HEADERS)
    assert resp.status_code == 404


async def test_get_room_usage_returns_200(client: AsyncClient):
    """GET /v1/usage/rooms/{room_id} returns 200 for a valid room."""
    room_resp = await client.post(
        "/rooms",
        json={"name": "per-room-test", "created_by": "agent"},
        headers=HEADERS,
    )
    room_id = room_resp.json()["id"]

    resp = await client.get(f"/v1/usage/rooms/{room_id}", headers=HEADERS)
    assert resp.status_code == 200


async def test_get_room_usage_schema(client: AsyncClient):
    """Per-room usage endpoint returns all required fields with correct types."""
    room_resp = await client.post(
        "/rooms",
        json={"name": "schema-check-room", "created_by": "agent"},
        headers=HEADERS,
    )
    room_id = room_resp.json()["id"]

    resp = await client.get(f"/v1/usage/rooms/{room_id}", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    required = {
        "room_id", "room_name", "snapshot_at",
        "message_count", "active_agents", "locked_files",
        "active_goal", "top_senders",
    }
    assert required.issubset(data.keys()), (
        f"Missing fields: {required - data.keys()}"
    )

    assert data["room_id"] == room_id
    assert data["room_name"] == "schema-check-room"
    assert isinstance(data["message_count"], int)
    assert isinstance(data["active_agents"], list)
    assert isinstance(data["locked_files"], dict)
    assert isinstance(data["top_senders"], list)


async def test_get_room_usage_message_count(client: AsyncClient):
    """Per-room message_count increments as messages are sent."""
    room_id = await _create_room_and_send(client, "per-room-count", "alpha", "beta", 5)

    resp = await client.get(f"/v1/usage/rooms/{room_id}", headers=HEADERS)
    assert resp.json()["message_count"] >= 5


async def test_get_room_usage_top_senders(client: AsyncClient):
    """Per-room top_senders lists agents who sent messages in that room."""
    room_id = await _create_room_and_send(client, "per-room-senders", "alpha", "gamma", 2)

    resp = await client.get(f"/v1/usage/rooms/{room_id}", headers=HEADERS)
    sender_names = [s["name"] for s in resp.json()["top_senders"]]
    assert "gamma" in sender_names


async def test_get_room_usage_locked_files_present(client: AsyncClient):
    """locked_files in per-room usage reflects active locks."""
    room_id = await _create_room_and_send(client, "lock-usage-room", "alpha", "beta", 0)

    # Acquire a lock
    await client.post(
        f"/rooms/{room_id}/lock",
        json={"file_path": "src/locked.py", "claimed_by": "alpha", "ttl_seconds": 300},
        headers=HEADERS,
    )

    resp = await client.get(f"/v1/usage/rooms/{room_id}", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "src/locked.py" in data["locked_files"]


async def test_get_room_usage_active_goal_present(client: AsyncClient):
    """active_goal in per-room usage reflects the goal set via PATCH."""
    room_id = await _create_room_and_send(client, "goal-usage-room", "alpha", "beta", 0)

    await client.patch(
        f"/rooms/{room_id}/state/goal",
        json={"goal": "implement the auth layer", "set_by": "alpha"},
        headers=HEADERS,
    )

    resp = await client.get(f"/v1/usage/rooms/{room_id}", headers=HEADERS)
    assert resp.json()["active_goal"] == "implement the auth layer"


async def test_get_room_usage_resolves_by_name(client: AsyncClient):
    """Per-room usage resolves room by name as well as UUID."""
    await client.post(
        "/rooms",
        json={"name": "named-usage-room", "created_by": "agent"},
        headers=HEADERS,
    )

    resp = await client.get("/v1/usage/rooms/named-usage-room", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["room_name"] == "named-usage-room"
