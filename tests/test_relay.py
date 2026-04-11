import asyncio
import sys
import time
import types
from datetime import datetime, timedelta, timezone
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


async def test_recipient_is_listed_as_participant(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "charlie", "to": "dave", "content": "hi"},
        headers=auth_headers,
    )
    resp = await client.get("/participants", headers=auth_headers)
    participants = resp.json()
    assert "dave" in participants


async def test_file_persistence_save_and_load(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    filepath = str(tmp_path / "messages.json")

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


async def test_delivered_messages_do_not_reappear_after_reload(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    filepath = str(tmp_path / "messages.json")

    with patch("relay_server.MESSAGES_FILE", filepath):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "deliver once"},
            headers=auth_headers,
        )

        resp = await client.get("/messages/bob", headers=auth_headers)
        assert [msg["content"] for msg in resp.json()] == ["deliver once"]

        _reset_state()
        _load_from_file()

        resp = await client.get("/messages/bob", headers=auth_headers)
        assert resp.json() == []


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


async def test_large_unicode_message_is_chunked_without_corruption(
    client: AsyncClient, auth_headers: dict
):
    """Chunking should preserve UTF-8 content across chunk boundaries."""
    large_content = "🙂" * 60

    with patch("relay_server.MAX_MESSAGE_SIZE", 101):
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


async def test_chunked_message_too_large_for_storage_cap_returns_413(
    client: AsyncClient, auth_headers: dict
):
    """Reject chunked messages that cannot fit within the configured global cap."""
    with patch("relay_server.MAX_MESSAGE_SIZE", 4), patch("relay_server.MAX_MESSAGES", 2):
        resp = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "abcdefghij"},
            headers=auth_headers,
        )

    assert resp.status_code == 413


async def test_message_cap_does_not_split_chunk_groups(client: AsyncClient, auth_headers: dict):
    """When trimming is needed, whole chunked messages should be kept or removed together."""
    with patch("relay_server.MAX_MESSAGE_SIZE", 4), patch("relay_server.MAX_MESSAGES", 4):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "one"},
            headers=auth_headers,
        )
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "two"},
            headers=auth_headers,
        )
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "abcdefghij"},
            headers=auth_headers,
        )

        resp = await client.get("/messages/bob", headers=auth_headers)
        messages = resp.json()

    assert [msg["content"] for msg in messages] == ["two", "abcdefghij"]


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
    base_time = datetime.now(timezone.utc)

    async with locks["bob"]:
        message_queues["bob"].append({
            "id": "chunk-1",
            "from_name": "alice",
            "to": "bob",
            "content": "part1",
            "timestamp": base_time.isoformat(),
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
            "timestamp": (base_time + timedelta(seconds=1)).isoformat(),
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


async def test_message_event_is_rearmed_by_consumer(client: AsyncClient, auth_headers: dict):
    """Senders set the event; consumers clear it after observing the queue state."""
    from relay_server import message_events

    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    assert message_events["bob"].is_set()

    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.status_code == 200
    assert not message_events["bob"].is_set()


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
    """Wait values above 60 should be clamped. Use wait=2 to keep test fast."""
    start = time.monotonic()
    resp = await client.get("/messages/nobody?wait=2", headers=auth_headers)
    elapsed = time.monotonic() - start
    assert resp.status_code == 200
    assert resp.json() == []
    # Should have waited ~2s, not longer
    assert elapsed < 5


async def test_no_wait_param_returns_immediately(client: AsyncClient, auth_headers: dict):
    """Without wait param, should return immediately even if empty."""
    resp = await client.get("/messages/nobody", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_register_webhook(client: AsyncClient, auth_headers: dict):
    """Should register a webhook for an instance."""
    resp = await client.post(
        "/webhooks",
        json={"instance_name": "bob", "callback_url": "https://example.com/incoming"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "registered"


async def test_register_webhook_allows_http_ip_callback(client: AsyncClient, auth_headers: dict):
    """Public HTTP callback URLs should be accepted."""
    resp = await client.post(
        "/webhooks",
        json={"instance_name": "bob", "callback_url": "http://100.127.251.47:9000/incoming"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "registered"


async def test_register_webhook_rejects_localhost(client: AsyncClient, auth_headers: dict):
    """Webhook targets must be public-facing to reduce SSRF risk."""
    resp = await client.post(
        "/webhooks",
        json={"instance_name": "bob", "callback_url": "http://localhost:9999/incoming"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_delete_webhook(client: AsyncClient, auth_headers: dict):
    """Should deregister a webhook."""
    await client.post(
        "/webhooks",
        json={"instance_name": "bob", "callback_url": "https://example.com/incoming"},
        headers=auth_headers,
    )
    resp = await client.delete("/webhooks/bob", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"


async def test_delete_nonexistent_webhook(client: AsyncClient, auth_headers: dict):
    """Deleting a non-existent webhook should still return 200."""
    resp = await client.delete("/webhooks/nobody", headers=auth_headers)
    assert resp.status_code == 200


def test_main_runs_uvicorn(monkeypatch):
    """CLI entrypoint should invoke uvicorn with the configured port."""
    from relay_server import main

    calls = []

    def fake_run(app_arg, host: str, port: int):
        calls.append((app_arg, host, port))

    monkeypatch.setenv("PORT", "9090")
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=fake_run))

    main()

    assert calls == [(app, "0.0.0.0", 9090)]


async def test_webhook_called_on_message(client: AsyncClient, auth_headers: dict):
    """When a webhook is registered, sending a message should POST to the callback."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch as mock_patch

    # Register webhook
    await client.post(
        "/webhooks",
        json={"instance_name": "bob", "callback_url": "https://example.com/incoming"},
        headers=auth_headers,
    )

    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)

    with mock_patch("relay_server.httpx.AsyncClient", return_value=mock_client_instance):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "push this"},
            headers=auth_headers,
        )

    mock_client_instance.post.assert_called_once()
    call_args = mock_client_instance.post.call_args
    assert call_args[0][0] == "https://example.com/incoming"
    assert call_args[1]["json"]["content"] == "push this"


async def test_send_rejects_empty_name(client: AsyncClient, auth_headers: dict):
    """Empty from_name or to should be rejected."""
    resp = await client.post(
        "/messages",
        json={"from_name": "", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_send_rejects_special_characters_in_name(client: AsyncClient, auth_headers: dict):
    """Names with special characters should be rejected."""
    resp = await client.post(
        "/messages",
        json={"from_name": "alice<script>", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_send_rejects_overly_long_name(client: AsyncClient, auth_headers: dict):
    """Names exceeding MAX_NAME_LENGTH should be rejected."""
    resp = await client.post(
        "/messages",
        json={"from_name": "a" * 65, "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_send_accepts_valid_names(client: AsyncClient, auth_headers: dict):
    """Alphanumeric names with hyphens and underscores should be accepted."""
    resp = await client.post(
        "/messages",
        json={"from_name": "alice-01_test", "to": "bob-2", "content": "hi"},
        headers=auth_headers,
    )
    assert resp.status_code == 200


async def test_message_ttl_expires_old_messages(client: AsyncClient, auth_headers: dict):
    """Messages older than MESSAGE_TTL_SECONDS should be removed on next trim."""
    from relay_server import locks, message_queues

    old_timestamp = "2020-01-01T00:00:00+00:00"
    async with locks["bob"]:
        message_queues["bob"].append({
            "id": "old-msg",
            "from_name": "alice",
            "to": "bob",
            "content": "stale",
            "timestamp": old_timestamp,
        })

    # Send a new message to trigger trimming
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "fresh"},
        headers=auth_headers,
    )

    resp = await client.get("/messages/bob", headers=auth_headers)
    messages = resp.json()
    contents = [m["content"] for m in messages]
    assert "stale" not in contents
    assert "fresh" in contents


async def test_message_ttl_is_enforced_on_read_without_new_send(
    client: AsyncClient, auth_headers: dict
):
    """Expired messages should not be returned even if no later send triggers trim."""
    from relay_server import locks, message_queues

    async with locks["bob"]:
        message_queues["bob"].append({
            "id": "old-msg",
            "from_name": "alice",
            "to": "bob",
            "content": "stale",
            "timestamp": "2020-01-01T00:00:00+00:00",
        })

    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.json() == []


async def test_analytics_excludes_expired_messages(client: AsyncClient, auth_headers: dict):
    """Expired messages should not contribute to pending analytics totals."""
    from relay_server import locks, message_queues

    async with locks["bob"]:
        message_queues["bob"].append({
            "id": "old-msg",
            "from_name": "alice",
            "to": "bob",
            "content": "stale",
            "timestamp": "2020-01-01T00:00:00+00:00",
        })

    resp = await client.get("/analytics", headers=auth_headers)
    data = resp.json()
    assert data["messages_pending"] == 0


async def test_create_room(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/rooms",
        json={"name": "dev-room", "created_by": "alice"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "dev-room"
    assert "alice" in data["members"]
    assert "id" in data
    assert "created_at" in data


async def test_create_room_duplicate_name_rejected(
    client: AsyncClient, auth_headers: dict
):
    await client.post(
        "/rooms",
        json={"name": "dev-room", "created_by": "alice"},
        headers=auth_headers,
    )
    resp = await client.post(
        "/rooms",
        json={"name": "dev-room", "created_by": "bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


async def test_list_rooms(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/rooms",
        json={"name": "room-a", "created_by": "alice"},
        headers=auth_headers,
    )
    await client.post(
        "/rooms",
        json={"name": "room-b", "created_by": "bob"},
        headers=auth_headers,
    )
    resp = await client.get("/rooms", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    names = {r["name"] for r in resp.json()}
    assert names == {"room-a", "room-b"}


async def test_get_room_by_id(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post(
        "/rooms",
        json={"name": "my-room", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "my-room"


async def test_get_nonexistent_room_returns_404(
    client: AsyncClient, auth_headers: dict
):
    resp = await client.get("/rooms/nonexistent-id", headers=auth_headers)
    assert resp.status_code == 404


async def test_join_room(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post(
        "/rooms",
        json={"name": "my-room", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    resp = await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "joined"
    resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)
    assert "bob" in resp.json()["members"]


async def test_leave_room(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post(
        "/rooms",
        json={"name": "my-room", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=auth_headers,
    )
    resp = await client.post(
        f"/rooms/{room_id}/leave",
        json={"participant": "bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "left"
    resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)
    assert "bob" not in resp.json()["members"]


async def test_room_endpoints_require_auth(client: AsyncClient):
    resp = await client.post(
        "/rooms", json={"name": "x", "created_by": "y"}
    )
    assert resp.status_code == 401
    resp = await client.get("/rooms")
    assert resp.status_code == 401


async def test_send_room_message_fans_out(client: AsyncClient, auth_headers: dict):
    """Message sent to room should appear in all members' inboxes except sender."""
    create_resp = await client.post(
        "/rooms", json={"name": "team", "created_by": "alice"}, headers=auth_headers
    )
    room_id = create_resp.json()["id"]
    await client.post(f"/rooms/{room_id}/join", json={"participant": "bob"}, headers=auth_headers)
    await client.post(
        f"/rooms/{room_id}/join", json={"participant": "charlie"}, headers=auth_headers
    )

    resp = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "hello team"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Bob gets it
    resp = await client.get("/messages/bob", headers=auth_headers)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello team"
    assert msgs[0]["from_name"] == "alice"
    assert msgs[0]["room"] == "team"

    # Charlie gets it
    resp = await client.get("/messages/charlie", headers=auth_headers)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["room"] == "team"

    # Alice also sees her own message (room shows all traffic)
    resp = await client.get("/messages/alice", headers=auth_headers)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello team"


async def test_room_message_with_type(client: AsyncClient, auth_headers: dict):
    """Room message should preserve custom message_type."""
    create_resp = await client.post(
        "/rooms", json={"name": "team", "created_by": "alice"}, headers=auth_headers
    )
    room_id = create_resp.json()["id"]
    await client.post(f"/rooms/{room_id}/join", json={"participant": "bob"}, headers=auth_headers)

    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "CLAIMING: auth module", "message_type": "claim"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.json()[0]["message_type"] == "claim"


async def test_room_message_nonmember_rejected(client: AsyncClient, auth_headers: dict):
    """Non-members should be rejected with 403."""
    create_resp = await client.post(
        "/rooms", json={"name": "private", "created_by": "alice"}, headers=auth_headers
    )
    room_id = create_resp.json()["id"]
    resp = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "eve", "content": "let me in"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


async def test_room_message_nonexistent_room_returns_404(
    client: AsyncClient, auth_headers: dict
):
    """Sending to a nonexistent room should return 404."""
    resp = await client.post(
        "/rooms/fake-room/messages",
        json={"from_name": "alice", "content": "hello"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_dm_still_works_after_rooms_added(client: AsyncClient, auth_headers: dict):
    """Direct messages should still work and have room=None."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "direct msg"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob", headers=auth_headers)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "direct msg"
    assert msgs[0].get("room") is None


async def test_room_persistence_save_and_load(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    filepath = str(tmp_path / "messages.json")
    with patch("relay_server.MESSAGES_FILE", filepath):
        await client.post(
            "/rooms",
            json={"name": "test-room", "created_by": "alice"},
            headers=auth_headers,
        )
        _save_to_file()
        _reset_state()
        _load_from_file()
        resp = await client.get("/rooms", headers=auth_headers)
        room_list = resp.json()
        assert len(room_list) == 1
        assert room_list[0]["name"] == "test-room"
        assert "alice" in room_list[0]["members"]


async def test_sse_queue_receives_dm(client: AsyncClient, auth_headers: dict):
    """SSE queue should receive DMs pushed after subscription."""
    from relay_server import sse_queues

    q: asyncio.Queue = asyncio.Queue()
    sse_queues["bob"].append(q)

    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "sse test"},
        headers=auth_headers,
    )

    msg = await asyncio.wait_for(q.get(), timeout=2)
    assert msg["content"] == "sse test"
    assert msg["from_name"] == "alice"

    sse_queues["bob"].remove(q)


async def test_sse_queue_receives_room_messages(
    client: AsyncClient, auth_headers: dict
):
    """SSE queue should receive room fan-out messages."""
    from relay_server import sse_queues

    q: asyncio.Queue = asyncio.Queue()
    sse_queues["bob"].append(q)

    create_resp = await client.post(
        "/rooms",
        json={"name": "sse-room", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=auth_headers,
    )

    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "room sse test"},
        headers=auth_headers,
    )

    msg = await asyncio.wait_for(q.get(), timeout=2)
    assert msg["content"] == "room sse test"
    assert msg["room"] == "sse-room"

    sse_queues["bob"].remove(q)


async def test_sse_endpoint_registers_and_cleans_up_queue(
    client: AsyncClient, auth_headers: dict
):
    """SSE stream endpoint should register a queue for the recipient and clean up on disconnect."""
    from relay_server import sse_queues, stream_messages

    # Directly invoke the endpoint handler and iterate its generator
    resp = await stream_messages(recipient="bob", token="test-secret")
    assert resp.media_type == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache"

    # The queue should now be registered
    assert len(sse_queues["bob"]) == 1
    q = sse_queues["bob"][0]

    # Iterate the generator to get the connected event
    gen = resp.body_iterator
    first_chunk = await gen.__anext__()
    assert "event: connected" in first_chunk
    assert "bob" in first_chunk

    # Push a message and verify the generator yields it
    test_msg = {"from_name": "alice", "content": "hello"}
    await q.put(test_msg)
    second_chunk = await gen.__anext__()
    assert "event: message" in second_chunk
    assert "hello" in second_chunk

    # Close the generator to trigger cleanup
    await gen.aclose()
    assert q not in sse_queues.get("bob", [])


async def test_sse_endpoint_rejects_bad_token(client: AsyncClient):
    """SSE endpoint should reject invalid tokens."""
    resp = await client.get("/stream/bob", params={"token": "wrong"})
    assert resp.status_code == 401


async def test_room_history_returns_messages(client: AsyncClient, auth_headers: dict):
    """History endpoint should return messages sent to a room."""
    create_resp = await client.post(
        "/rooms", json={"name": "hist-room", "created_by": "alice"}, headers=auth_headers
    )
    room_id = create_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join", json={"participant": "bob"}, headers=auth_headers
    )

    for i in range(3):
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "alice", "content": f"msg-{i}"},
            headers=auth_headers,
        )

    resp = await client.get(f"/rooms/{room_id}/history", headers=auth_headers)
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 3
    assert msgs[0]["content"] == "msg-0"
    assert msgs[2]["content"] == "msg-2"
    assert msgs[0]["from_name"] == "alice"
    assert msgs[0]["room"] == "hist-room"


async def test_room_history_not_cleared_on_read(client: AsyncClient, auth_headers: dict):
    """History should persist even after members read their inboxes."""
    create_resp = await client.post(
        "/rooms", json={"name": "persist-room", "created_by": "alice"}, headers=auth_headers
    )
    room_id = create_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join", json={"participant": "bob"}, headers=auth_headers
    )

    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "hello"},
        headers=auth_headers,
    )

    # Read clears inbox
    await client.get("/messages/bob", headers=auth_headers)
    await client.get("/messages/alice", headers=auth_headers)

    # History still has the message
    resp = await client.get(f"/rooms/{room_id}/history", headers=auth_headers)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello"


async def test_room_history_limit_param(client: AsyncClient, auth_headers: dict):
    """Limit parameter should cap the number of returned messages."""
    create_resp = await client.post(
        "/rooms", json={"name": "limit-room", "created_by": "alice"}, headers=auth_headers
    )
    room_id = create_resp.json()["id"]

    for i in range(10):
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "alice", "content": f"msg-{i}"},
            headers=auth_headers,
        )

    resp = await client.get(
        f"/rooms/{room_id}/history", params={"limit": 3}, headers=auth_headers
    )
    msgs = resp.json()
    assert len(msgs) == 3
    # Should return the LAST 3
    assert msgs[0]["content"] == "msg-7"
    assert msgs[2]["content"] == "msg-9"


async def test_room_history_resolve_by_name(client: AsyncClient, auth_headers: dict):
    """History endpoint should resolve rooms by name, not just ID."""
    await client.post(
        "/rooms", json={"name": "named-room", "created_by": "alice"}, headers=auth_headers
    )

    await client.post(
        "/rooms/named-room/messages",
        json={"from_name": "alice", "content": "by-name"},
        headers=auth_headers,
    )

    resp = await client.get("/rooms/named-room/history", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_room_history_nonexistent_room(client: AsyncClient, auth_headers: dict):
    """History for a nonexistent room should return 404."""
    resp = await client.get("/rooms/no-such-room/history", headers=auth_headers)
    assert resp.status_code == 404


async def test_room_history_requires_auth(client: AsyncClient):
    """History endpoint should require authentication."""
    resp = await client.get("/rooms/any-room/history")
    assert resp.status_code == 401


async def test_room_history_persists(client: AsyncClient, auth_headers: dict, tmp_path):
    """Room history should survive save/load cycle."""
    filepath = str(tmp_path / "messages.json")
    with patch("relay_server.MESSAGES_FILE", filepath):
        create_resp = await client.post(
            "/rooms", json={"name": "save-room", "created_by": "alice"},
            headers=auth_headers,
        )
        room_id = create_resp.json()["id"]

        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "alice", "content": "persisted"},
            headers=auth_headers,
        )

        _save_to_file()
        _reset_state()
        _load_from_file()

        from relay_server import room_history
        assert len(room_history[room_id]) == 1
        assert room_history[room_id][0]["content"] == "persisted"
