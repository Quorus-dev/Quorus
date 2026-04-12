import contextlib
from contextlib import asynccontextmanager, suppress
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import murmur.mcp_server as mcp_server


@pytest.fixture(autouse=True)
def configure_mcp():
    """Patch module-level constants for all tests."""
    patches = [
        patch.object(mcp_server, "RELAY_URL", "http://relay:8080"),
        patch.object(mcp_server, "RELAY_SECRET", "secret"),
        patch.object(mcp_server, "INSTANCE_NAME", "alice"),
        patch.object(mcp_server, "POLL_MODE", "lazy"),
        patch.object(mcp_server, "PUSH_NOTIFICATION_METHOD", None),
        patch.object(mcp_server, "PUSH_NOTIFICATION_CHANNEL", "mcp-tunnel"),
    ]
    # API_KEY added in newer mcp.py — patch only if present
    if hasattr(mcp_server, "API_KEY"):
        patches.append(patch.object(mcp_server, "API_KEY", ""))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        mcp_server._reset_runtime_state()
        yield
        mcp_server._reset_runtime_state()


def _make_mock_client(mock_method: str, return_value):
    """Helper to create a mock httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = return_value
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.is_closed = False
    setattr(mock_client, mock_method, AsyncMock(return_value=mock_response))
    return mock_client


async def test_send_message_calls_relay():
    """send_message should POST to relay with correct payload."""
    mock_client = _make_mock_client(
        "post", {"id": "test-id", "timestamp": "2026-04-05T00:00:00Z"}
    )

    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._send_message("bob", "hello")

        mock_client.post.assert_called_once_with(
            "http://relay:8080/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello"},
            headers={"Authorization": "Bearer secret"},
        )
        assert "test-id" in result


async def test_check_messages_calls_relay():
    """check_messages should GET from relay for this instance."""
    # Manual ACK response format: {"messages": [...], "ack_token": "..."}
    mock_client = _make_mock_client(
        "get",
        {
            "messages": [
                {"id": "1", "from_name": "bob", "content": "hi",
                 "timestamp": "2026-04-05T00:00:00Z"}
            ],
            "ack_token": "tok123",
        },
    )
    # Also mock the ACK POST call
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._check_messages()

        mock_client.get.assert_called_once_with(
            "http://relay:8080/messages/alice",
            params={"wait": 0, "ack": "manual"},
            headers={"Authorization": "Bearer secret"},
            timeout=10,
        )
        # Should ACK after processing
        mock_client.post.assert_called_once()
        assert "bob" in result


async def test_check_messages_always_uses_nonblocking_fetch():
    """check_messages always uses wait=0 — SSE handles live delivery."""
    # Empty messages response (no ACK needed when no messages)
    mock_client = _make_mock_client("get", {"messages": [], "ack_token": None})

    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._check_messages()

    mock_client.get.assert_called_once_with(
        "http://relay:8080/messages/alice",
        params={"wait": 0, "ack": "manual"},
        headers={"Authorization": "Bearer secret"},
        timeout=10,
    )
    assert result == "No new messages."


async def test_list_participants_calls_relay():
    """list_participants should GET /participants from relay."""
    mock_client = _make_mock_client("get", ["alice", "bob"])

    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._list_participants()

        mock_client.get.assert_called_once_with(
            "http://relay:8080/participants",
            headers={"Authorization": "Bearer secret"},
        )
        assert "alice" in result
        assert "bob" in result


async def test_send_message_relay_unreachable():
    """send_message should return error when relay is down."""
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post.side_effect = httpx.ConnectError("Connection refused")

    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._send_message("bob", "hello")
        assert "error" in result.lower() or "cannot" in result.lower()


async def test_check_messages_always_fetches_from_durable_queue():
    """check_messages should always fetch from durable queue, SSE is notification-only."""
    # Put messages in SSE buffer (these should be ignored/cleared)
    await mcp_server._append_pending_messages([
        {
            "id": "sse-msg",
            "from_name": "sse_sender",
            "content": "sse notification",
            "timestamp": "2026-04-08T12:00:00Z",
        }
    ])

    # Mock the HTTP client to return messages from durable queue
    mock_client = _make_mock_client("get", {
        "messages": [
            {
                "id": "queue-msg",
                "from_name": "alice",
                "content": "from durable queue",
                "timestamp": "2026-04-08T12:00:00Z",
            }
        ],
        "ack_token": "test-ack-token",
    })
    # Also mock the ACK endpoint
    mock_ack_response = MagicMock(status_code=200, raise_for_status=MagicMock())
    mock_client.post = AsyncMock(return_value=mock_ack_response)

    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._check_messages()

    # HTTP get should be called (always fetch from durable queue)
    mock_client.get.assert_called_once()
    # Result should contain the durable queue message, not the SSE message
    assert "alice" in result
    assert "from durable queue" in result


async def test_send_push_notification_uses_configured_method():
    """Push notifications should be emitted using the configured raw JSON-RPC method."""
    session = MagicMock()
    session.send_message = AsyncMock()

    with patch.object(mcp_server, "PUSH_NOTIFICATION_METHOD", "notifications/custom/inbox"):
        await mcp_server._send_push_notification(
            session,
            {
                "id": "msg-1",
                "from_name": "alice",
                "content": "hello",
                "timestamp": "2026-04-08T12:00:00Z",
            },
        )

    session.send_message.assert_awaited_once()
    session_message = session.send_message.await_args.args[0]
    notification = session_message.message.root
    assert notification.method == "notifications/custom/inbox"
    assert notification.params["channel"] == "mcp-tunnel"
    assert "alice: hello" in notification.params["message"]


@pytest.mark.parametrize(
    "relay_url",
    [
        "",
        "http://",
        "https://",
        "ftp://relay.example",
        "http://user:pass@relay.example",
    ],
)
def test_validate_relay_url_rejects_invalid_values(relay_url: str):
    with pytest.raises(SystemExit, match="Invalid relay_url"):
        mcp_server._validate_relay_url(relay_url)


@pytest.mark.parametrize(
    "relay_url",
    [
        "http://relay.example",
        "https://relay.example",
        "https://relay.example/base",
        "http://127.0.0.1:8080",
    ],
)
def test_validate_relay_url_accepts_http_urls(relay_url: str):
    assert mcp_server._validate_relay_url(relay_url) == relay_url


async def test_send_room_message_posts_to_relay():
    mock_client = _make_mock_client(
        "post", {"id": "msg-1", "timestamp": "2026-04-11T00:00:00Z"}
    )
    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._send_room_message("room-123", "hello room")
    assert "msg-1" in result
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "/rooms/room-123/messages" in call_args[0][0]
    assert call_args[1]["json"]["from_name"] == "alice"


async def test_join_room_posts_to_relay():
    mock_client = _make_mock_client("post", {"status": "joined"})
    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._join_room("room-123")
    assert "joined" in result.lower()


async def test_list_rooms_gets_from_relay():
    mock_rooms = [
        {
            "id": "r1",
            "name": "yc-hack",
            "members": ["alice", "bob"],
            "created_at": "2026-04-11T00:00:00Z",
        }
    ]
    mock_client = _make_mock_client("get", mock_rooms)
    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._list_rooms()
    assert "yc-hack" in result


async def test_process_sse_message_event():
    await mcp_server._process_sse_event(
        "message",
        '{"id": "m1", "from_name": "alice", "content": "hi", "timestamp": "2026-04-11T00:00:00Z"}',
    )
    messages = await mcp_server._drain_pending_messages()
    assert len(messages) == 1
    assert messages[0]["from_name"] == "alice"


async def test_process_sse_ignores_connected_event():
    await mcp_server._process_sse_event("connected", '{"participant": "bob"}')
    messages = await mcp_server._drain_pending_messages()
    assert len(messages) == 0


async def test_sse_listener_is_only_delivery_mode():
    """SSE is the only background delivery mechanism — no interval polling."""
    # start_auto_poll and stop_auto_poll no longer exist.
    assert not hasattr(mcp_server, "_auto_poll_task")
    assert not hasattr(mcp_server, "_auto_poll_stop")
    assert not hasattr(mcp_server, "_start_auto_poll")
    assert not hasattr(mcp_server, "_stop_auto_poll")


async def test_auto_poll_delivers_messages():
    """Auto-poll should fetch and buffer messages."""
    msgs = [
        {"id": "m1", "from_name": "bob", "content": "hi",
         "timestamp": "2026-04-11T00:00:00Z"},
    ]
    # Manual ACK response format
    mock_client2 = _make_mock_client("get", {"messages": msgs, "ack_token": "tok123"})
    with patch("murmur.mcp_server._get_http_client", return_value=mock_client2):
        fetched, ack_token, err = await mcp_server._fetch_relay_messages(wait=0)
    assert len(fetched) == 1
    assert ack_token == "tok123"
    await mcp_server._append_pending_messages(fetched)
    buffered = await mcp_server._drain_pending_messages()
    assert len(buffered) == 1
    assert buffered[0]["from_name"] == "bob"


async def test_sse_listener_parses_and_buffers():
    """SSE listener should parse SSE format and buffer messages.

    Mocks the httpx streaming response to simulate the relay's SSE
    output, verifying the parser handles the event/data/blank-line
    protocol and calls _process_sse_event correctly.
    """
    import asyncio
    import json as json_mod

    msg_payload = json_mod.dumps({
        "id": "m1",
        "from_name": "bob",
        "content": "instant delivery",
        "timestamp": "2026-04-11T00:00:00Z",
    })

    sse_lines = [
        "event: connected",
        'data: {"participant": "alice"}',
        "",
        "event: message",
        f"data: {msg_payload}",
        "",
    ]

    async def fake_aiter_lines():
        for line in sse_lines:
            yield line
        # Keep alive until stop
        while True:
            await asyncio.sleep(10)

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.aiter_lines = fake_aiter_lines
    mock_resp.aclose = AsyncMock()

    @asynccontextmanager
    async def fake_stream(*args, **kwargs):
        yield mock_resp

    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.stream = fake_stream

    with patch("murmur.mcp_server._get_http_client", return_value=mock_client):
        stop = asyncio.Event()
        task = asyncio.create_task(mcp_server._sse_listener(stop))

        # Wait for the listener to process the SSE events
        for _ in range(20):
            msgs = await mcp_server._drain_pending_messages()
            if msgs:
                break
            await asyncio.sleep(0.05)

        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert len(msgs) == 1
    assert msgs[0]["from_name"] == "bob"
    assert msgs[0]["content"] == "instant delivery"


async def test_sse_listener_reconnects_on_error():
    """SSE listener should reconnect after a connection error."""
    import asyncio

    call_count = 0

    @asynccontextmanager
    async def failing_then_ok_stream(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Connection refused")
        # Second call succeeds with a message
        msg = (
            '{"id":"m1","from_name":"bob",'
            '"content":"after reconnect",'
            '"timestamp":"2026-04-11T00:00:00Z"}'
        )

        async def fake_lines():
            yield "event: connected"
            yield 'data: {"participant": "alice"}'
            yield ""
            yield "event: message"
            yield f"data: {msg}"
            yield ""
            while True:
                await asyncio.sleep(10)

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.aiter_lines = fake_lines
        mock_resp.aclose = AsyncMock()
        yield mock_resp

    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.stream = failing_then_ok_stream

    with patch(
        "murmur.mcp_server._get_http_client", return_value=mock_client
    ):
        stop = asyncio.Event()
        task = asyncio.create_task(mcp_server._sse_listener(stop))

        for _ in range(40):
            msgs = await mcp_server._drain_pending_messages()
            if msgs:
                break
            await asyncio.sleep(0.1)

        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert call_count >= 2
    assert len(msgs) == 1
    assert msgs[0]["content"] == "after reconnect"


# ── search_room and room_metrics MCP tool tests ─────────────────────────

async def test_search_room_by_keyword():
    search_results = [
        {"timestamp": "2026-04-11T08:00:00Z", "from_name": "alice",
         "content": "hello world", "message_type": "chat"},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = search_results
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("murmur.mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server.search_room("dev", q="hello")

    assert "alice" in result
    assert "hello world" in result


async def test_search_room_no_results():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("murmur.mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server.search_room("dev", q="nonexistent")

    assert "No matching" in result


async def test_room_metrics_returns_stats():
    messages = [
        {"from_name": "alice", "content": "hello", "message_type": "chat"},
        {"from_name": "bob", "content": "CLAIM: auth", "message_type": "claim"},
        {"from_name": "bob", "content": "STATUS: auth complete", "message_type": "status"},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = messages
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("murmur.mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server.room_metrics("dev")

    assert "3 messages" in result
    assert "alice" in result
    assert "bob" in result
    assert "Task completion: 1/1" in result


async def test_room_metrics_empty():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("murmur.mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server.room_metrics("empty")

    assert "No messages" in result
