import asyncio
import os
import tempfile
import time
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from relay_server import _load_from_file, _reset_state, _save_to_file, app


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-secret"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_check(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_no_auth_returns_401(client: AsyncClient):
    resp = await client.get("/participants")
    assert resp.status_code == 401


async def test_wrong_auth_returns_401(client: AsyncClient):
    resp = await client.get("/participants", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


async def test_correct_auth_returns_200(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/participants", headers=auth_headers)
    assert resp.status_code == 200


async def test_send_message(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hello bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert "timestamp" in data


async def test_receive_messages(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "msg1"},
        headers=auth_headers,
    )
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "msg2"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 2
    assert messages[0]["content"] == "msg1"
    assert messages[1]["content"] == "msg2"
    assert messages[0]["from_name"] == "alice"
    assert "id" in messages[0]
    assert "timestamp" in messages[0]


async def test_receive_clears_messages(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hello"},
        headers=auth_headers,
    )
    await client.get("/messages/bob", headers=auth_headers)
    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.json() == []


async def test_receive_no_messages(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/messages/nobody", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_send_registers_participant(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "charlie", "to": "dave", "content": "hi"},
        headers=auth_headers,
    )
    resp = await client.get("/participants", headers=auth_headers)
    participants = resp.json()
    assert "charlie" in participants


async def test_file_persistence_save_and_load(client: AsyncClient, auth_headers: dict):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        filepath = f.name

    with patch("relay_server.MESSAGES_FILE", filepath):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "persist me"},
            headers=auth_headers,
        )
        _save_to_file()

        _reset_state()
        _load_from_file()

        resp = await client.get("/messages/bob", headers=auth_headers)
        messages = resp.json()
        assert len(messages) == 1
        assert messages[0]["content"] == "persist me"

    os.unlink(filepath)


async def test_message_cap_trims_oldest(client: AsyncClient, auth_headers: dict):
    with patch("relay_server.MAX_MESSAGES", 5):
        for i in range(7):
            await client.post(
                "/messages",
                json={"from_name": "alice", "to": "bob", "content": f"msg-{i}"},
                headers=auth_headers,
            )

        resp = await client.get("/messages/bob", headers=auth_headers)
        messages = resp.json()
        assert len(messages) == 5
        assert messages[0]["content"] == "msg-2"
        assert messages[-1]["content"] == "msg-6"


async def test_concurrent_sends_no_data_loss(client: AsyncClient, auth_headers: dict):
    """Send 20 messages concurrently to same recipient, verify none lost."""

    async def send_one(i: int):
        await client.post(
            "/messages",
            json={"from_name": f"sender-{i}", "to": "target", "content": f"msg-{i}"},
            headers=auth_headers,
        )

    await asyncio.gather(*[send_one(i) for i in range(20)])

    resp = await client.get("/messages/target", headers=auth_headers)
    messages = resp.json()
    assert len(messages) == 20
    contents = {m["content"] for m in messages}
    assert contents == {f"msg-{i}" for i in range(20)}


async def test_small_message_has_no_chunk_fields(client: AsyncClient, auth_headers: dict):
    """Messages under the size limit should have no chunk metadata."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "small"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob", headers=auth_headers)
    msg = resp.json()[0]
    assert "chunk_group" not in msg


async def test_large_message_is_chunked_and_reassembled(client: AsyncClient, auth_headers: dict):
    """A message exceeding MAX_MESSAGE_SIZE should be chunked on send and reassembled on fetch."""
    large_content = "x" * 200

    with patch("relay_server.MAX_MESSAGE_SIZE", 100):
        resp = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": large_content},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        resp = await client.get("/messages/bob", headers=auth_headers)
        messages = resp.json()
        assert len(messages) == 1
        assert messages[0]["content"] == large_content
        assert "chunk_group" not in messages[0]


async def test_analytics_empty(client: AsyncClient, auth_headers: dict):
    """Analytics endpoint should return zeros when no messages have been sent."""
    resp = await client.get("/analytics", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_messages_sent"] == 0
    assert data["total_messages_delivered"] == 0
    assert data["messages_pending"] == 0
    assert data["participants"] == {}
    assert isinstance(data["hourly_volume"], list)
    assert "uptime_seconds" in data


async def test_analytics_after_send_and_receive(client: AsyncClient, auth_headers: dict):
    """Analytics should track sends and deliveries."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hello"},
        headers=auth_headers,
    )

    resp = await client.get("/analytics", headers=auth_headers)
    data = resp.json()
    assert data["total_messages_sent"] == 2
    assert data["total_messages_delivered"] == 0
    assert data["messages_pending"] == 2
    assert data["participants"]["alice"]["sent"] == 2

    # Now bob fetches
    await client.get("/messages/bob", headers=auth_headers)

    resp = await client.get("/analytics", headers=auth_headers)
    data = resp.json()
    assert data["total_messages_delivered"] == 2
    assert data["messages_pending"] == 0
    assert data["participants"]["bob"]["received"] == 2


async def test_analytics_hourly_volume(client: AsyncClient, auth_headers: dict):
    """Analytics should track hourly message volume."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    resp = await client.get("/analytics", headers=auth_headers)
    data = resp.json()
    assert len(data["hourly_volume"]) >= 1
    assert data["hourly_volume"][0]["count"] >= 1


async def test_analytics_requires_auth(client: AsyncClient):
    """Analytics endpoint should require auth."""
    resp = await client.get("/analytics")
    assert resp.status_code == 401


async def test_incomplete_chunks_held_back(client: AsyncClient, auth_headers: dict):
    """If not all chunks have arrived, they should not be returned."""
    from relay_server import locks, message_queues

    async with locks["bob"]:
        message_queues["bob"].append({
            "id": "chunk-1",
            "from_name": "alice",
            "to": "bob",
            "content": "part1",
            "timestamp": "2026-04-08T00:00:00Z",
            "chunk_group": "group-1",
            "chunk_index": 0,
            "chunk_total": 2,
        })

    resp = await client.get("/messages/bob", headers=auth_headers)
    messages = resp.json()
    assert len(messages) == 0

    async with locks["bob"]:
        message_queues["bob"].append({
            "id": "chunk-2",
            "from_name": "alice",
            "to": "bob",
            "content": "part2",
            "timestamp": "2026-04-08T00:00:01Z",
            "chunk_group": "group-1",
            "chunk_index": 1,
            "chunk_total": 2,
        })

    resp = await client.get("/messages/bob", headers=auth_headers)
    messages = resp.json()
    assert len(messages) == 1
    assert messages[0]["content"] == "part1part2"


async def test_long_poll_returns_immediately_with_messages(client: AsyncClient, auth_headers: dict):
    """Long-poll should return immediately if messages are already queued."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob?wait=10", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_long_poll_returns_empty_on_timeout(client: AsyncClient, auth_headers: dict):
    """Long-poll should return empty list after timeout if no messages arrive."""
    resp = await client.get("/messages/nobody?wait=1", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_long_poll_wakes_on_new_message(client: AsyncClient, auth_headers: dict):
    """Long-poll should return as soon as a message arrives."""

    async def poll():
        return await client.get("/messages/bob?wait=10", headers=auth_headers)

    async def send_delayed():
        await asyncio.sleep(0.5)
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "wake up"},
            headers=auth_headers,
        )

    start = time.monotonic()
    poll_task = asyncio.create_task(poll())
    send_task = asyncio.create_task(send_delayed())

    resp = await poll_task
    await send_task
    elapsed = time.monotonic() - start

    assert len(resp.json()) == 1
    assert resp.json()[0]["content"] == "wake up"
    assert elapsed < 5


async def test_long_poll_clamps_max_wait(client: AsyncClient, auth_headers: dict):
    """Wait values above 60 should be clamped to 60."""
    resp = await client.get("/messages/nobody?wait=120", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_no_wait_param_returns_immediately(client: AsyncClient, auth_headers: dict):
    """Without wait param, should return immediately even if empty."""
    resp = await client.get("/messages/nobody", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []
