from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import mcp_server


@pytest.fixture(autouse=True)
def configure_mcp():
    """Patch module-level constants for all tests."""
    with (
        patch.object(mcp_server, "RELAY_URL", "http://relay:8080"),
        patch.object(mcp_server, "RELAY_SECRET", "secret"),
        patch.object(mcp_server, "INSTANCE_NAME", "alice"),
        patch.object(mcp_server, "ENABLE_BACKGROUND_POLLING", False),
        patch.object(mcp_server, "PUSH_NOTIFICATION_METHOD", None),
        patch.object(mcp_server, "PUSH_NOTIFICATION_CHANNEL", "mcp-tunnel"),
    ):
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

    with patch("mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._send_message("bob", "hello")

        mock_client.post.assert_called_once_with(
            "http://relay:8080/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello"},
            headers={"Authorization": "Bearer secret"},
        )
        assert "test-id" in result


async def test_check_messages_calls_relay():
    """check_messages should GET from relay for this instance."""
    mock_client = _make_mock_client(
        "get",
        [{"id": "1", "from_name": "bob", "content": "hi", "timestamp": "2026-04-05T00:00:00Z"}],
    )

    with patch("mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._check_messages()

        mock_client.get.assert_called_once_with(
            "http://relay:8080/messages/alice",
            params={"wait": 30},
            headers={"Authorization": "Bearer secret"},
            timeout=35,
        )
        assert "bob" in result


async def test_check_messages_uses_nonblocking_fetch_when_background_polling_enabled():
    """Manual checks should not long-poll behind the background poller."""
    mock_client = _make_mock_client("get", [])

    with (
        patch.object(mcp_server, "ENABLE_BACKGROUND_POLLING", True),
        patch("mcp_server._get_http_client", return_value=mock_client),
    ):
        result = await mcp_server._check_messages()

    mock_client.get.assert_called_once_with(
        "http://relay:8080/messages/alice",
        params={"wait": 0},
        headers={"Authorization": "Bearer secret"},
        timeout=10,
    )
    assert result == "No new messages."


async def test_list_participants_calls_relay():
    """list_participants should GET /participants from relay."""
    mock_client = _make_mock_client("get", ["alice", "bob"])

    with patch("mcp_server._get_http_client", return_value=mock_client):
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

    with patch("mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._send_message("bob", "hello")
        assert "error" in result.lower() or "cannot" in result.lower()


async def test_check_messages_prefers_local_buffer():
    """Buffered channel messages should be returned before hitting the relay."""
    await mcp_server._append_pending_messages([
        {
            "id": "msg-1",
            "from_name": "alice",
            "content": "pushed",
            "timestamp": "2026-04-08T12:00:00Z",
        }
    ])

    mock_client = _make_mock_client("get", [])
    with patch("mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._check_messages()

    mock_client.get.assert_not_called()
    assert "alice" in result
    assert "pushed" in result


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
    with patch("mcp_server._get_http_client", return_value=mock_client):
        result = await mcp_server._send_room_message("room-123", "hello room")
    assert "msg-1" in result
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "/rooms/room-123/messages" in call_args[0][0]
    assert call_args[1]["json"]["from_name"] == "alice"


async def test_join_room_posts_to_relay():
    mock_client = _make_mock_client("post", {"status": "joined"})
    with patch("mcp_server._get_http_client", return_value=mock_client):
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
    with patch("mcp_server._get_http_client", return_value=mock_client):
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
