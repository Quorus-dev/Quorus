"""End-to-end test: multi-agent room conversation with fan-out."""

import asyncio
import time

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


async def test_multi_agent_room_conversation(client: AsyncClient):
    """Simulate a hackathon room with 3 agents coordinating."""
    resp = await client.post(
        "/rooms", json={"name": "yc-hack", "created_by": "arav-agent-1"}, headers=HEADERS
    )
    assert resp.status_code == 200
    room_id = resp.json()["id"]

    for agent in ["arav-agent-2", "aarya-agent-1", "aarya-agent-2"]:
        resp = await client.post(
            f"/rooms/{room_id}/join", json={"participant": agent}, headers=HEADERS
        )
        assert resp.status_code == 200

    # arav-agent-1 claims auth module
    resp = await client.post(
        f"/rooms/{room_id}/messages",
        json={
            "from_name": "arav-agent-1",
            "content": "CLAIMING: auth module",
            "message_type": "claim",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200

    # All other agents should see the claim
    for agent in ["arav-agent-2", "aarya-agent-1", "aarya-agent-2"]:
        resp = await client.get(f"/messages/{agent}?ack=server", headers=HEADERS)
        msgs = resp.json()
        assert len(msgs) == 1
        assert msgs[0]["message_type"] == "claim"
        assert msgs[0]["room"] == "yc-hack"
        assert msgs[0]["from_name"] == "arav-agent-1"

    # Sender also sees their own message (room shows all traffic)
    resp = await client.get("/messages/arav-agent-1?ack=server", headers=HEADERS)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["from_name"] == "arav-agent-1"

    # aarya-agent-1 sends status update
    resp = await client.post(
        f"/rooms/{room_id}/messages",
        json={
            "from_name": "aarya-agent-1",
            "content": "STATUS: database schema ready",
            "message_type": "status",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200

    # arav-agent-1 should see aarya's status
    resp = await client.get("/messages/arav-agent-1?ack=server", headers=HEADERS)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["from_name"] == "aarya-agent-1"
    assert msgs[0]["message_type"] == "status"

    # DMs still work alongside rooms
    resp = await client.post(
        "/messages",
        json={"from_name": "arav-agent-1", "to": "aarya-agent-1", "content": "private question"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    resp = await client.get("/messages/aarya-agent-1?ack=server", headers=HEADERS)
    msgs = resp.json()
    dm_msgs = [m for m in msgs if m.get("room") is None]
    assert len(dm_msgs) == 1
    assert dm_msgs[0]["content"] == "private question"

    # Analytics should reflect room activity
    resp = await client.get("/analytics", headers=HEADERS)
    data = resp.json()
    assert data["total_messages_sent"] >= 3

    # All agents are participants
    resp = await client.get("/participants", headers=HEADERS)
    participants = resp.json()
    assert "arav-agent-1" in participants
    assert "aarya-agent-1" in participants


async def test_sse_receives_room_fanout(client: AsyncClient):
    """SSE subscribers should receive room fan-out messages."""
    sse_svc = app.state.sse_service
    q = sse_svc.register_queue("_legacy", "bob")

    resp = await client.post(
        "/rooms", json={"name": "sse-test", "created_by": "alice"}, headers=HEADERS
    )
    assert resp.status_code == 200
    room_id = resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join", json={"participant": "bob"}, headers=HEADERS
    )

    resp = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "realtime test"},
        headers=HEADERS,
    )
    assert resp.status_code == 200

    msg = await asyncio.wait_for(q.get(), timeout=2)
    assert msg["content"] == "realtime test"
    assert msg["room"] == "sse-test"

    sse_svc.unregister_queue("_legacy", "bob", q)


async def test_two_rooms_isolation(client: AsyncClient):
    """Messages in one room should not leak to another room's members."""
    resp1 = await client.post(
        "/rooms", json={"name": "room-a", "created_by": "alice"}, headers=HEADERS
    )
    assert resp1.status_code == 200
    room_a = resp1.json()["id"]
    await client.post(f"/rooms/{room_a}/join", json={"participant": "bob"}, headers=HEADERS)

    resp2 = await client.post(
        "/rooms", json={"name": "room-b", "created_by": "charlie"}, headers=HEADERS
    )
    assert resp2.status_code == 200
    room_b = resp2.json()["id"]
    await client.post(f"/rooms/{room_b}/join", json={"participant": "dave"}, headers=HEADERS)

    resp = await client.post(
        f"/rooms/{room_a}/messages",
        json={"from_name": "alice", "content": "room-a only"},
        headers=HEADERS,
    )
    assert resp.status_code == 200

    resp = await client.get("/messages/bob?ack=server", headers=HEADERS)
    assert len(resp.json()) == 1

    resp = await client.get("/messages/dave?ack=server", headers=HEADERS)
    assert resp.json() == []

    resp = await client.get("/messages/charlie?ack=server", headers=HEADERS)
    assert resp.json() == []


async def test_concurrent_room_messages(client: AsyncClient):
    """Multiple concurrent messages to same room should not lose data."""
    resp = await client.post(
        "/rooms", json={"name": "concurrent", "created_by": "alice"}, headers=HEADERS
    )
    assert resp.status_code == 200
    room_id = resp.json()["id"]
    await client.post(f"/rooms/{room_id}/join", json={"participant": "bob"}, headers=HEADERS)

    async def send_one(i: int):
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "alice", "content": f"msg-{i}"},
            headers=HEADERS,
        )

    await asyncio.gather(*[send_one(i) for i in range(10)])

    resp = await client.get("/messages/bob?ack=server", headers=HEADERS)
    msgs = resp.json()
    assert len(msgs) == 10
    contents = {m["content"] for m in msgs}
    assert contents == {f"msg-{i}" for i in range(10)}


async def test_room_message_long_poll_wakes(client: AsyncClient):
    """Long-polling agent should wake when room message arrives."""
    resp = await client.post(
        "/rooms", json={"name": "poll-test", "created_by": "alice"}, headers=HEADERS
    )
    assert resp.status_code == 200
    room_id = resp.json()["id"]
    await client.post(f"/rooms/{room_id}/join", json={"participant": "bob"}, headers=HEADERS)

    async def poll():
        return await client.get("/messages/bob?wait=10&ack=server", headers=HEADERS)

    async def send_delayed():
        await asyncio.sleep(0.5)
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "alice", "content": "wake up bob"},
            headers=HEADERS,
        )

    start = time.monotonic()
    poll_task = asyncio.create_task(poll())
    send_task = asyncio.create_task(send_delayed())

    resp = await poll_task
    await send_task
    elapsed = time.monotonic() - start

    assert len(resp.json()) == 1
    assert resp.json()[0]["content"] == "wake up bob"
    assert elapsed < 5
