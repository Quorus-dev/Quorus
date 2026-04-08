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
    ):
        yield


def _make_mock_client(mock_method: str, return_value):
    """Helper to create a mock httpx.AsyncClient context manager."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = return_value
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    setattr(mock_client, mock_method, AsyncMock(return_value=mock_response))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


async def test_send_message_calls_relay():
    """send_message should POST to relay with correct payload."""
    mock_client = _make_mock_client(
        "post", {"id": "test-id", "timestamp": "2026-04-05T00:00:00Z"}
    )

    with patch("mcp_server.httpx.AsyncClient", return_value=mock_client):
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

    with patch("mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server._check_messages()

        mock_client.get.assert_called_once_with(
            "http://relay:8080/messages/alice?wait=30",
            headers={"Authorization": "Bearer secret"},
            timeout=35,
        )
        assert "bob" in result


async def test_list_participants_calls_relay():
    """list_participants should GET /participants from relay."""
    mock_client = _make_mock_client("get", ["alice", "bob"])

    with patch("mcp_server.httpx.AsyncClient", return_value=mock_client):
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
    mock_client.post.side_effect = httpx.ConnectError("Connection refused")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server._send_message("bob", "hello")
        assert "error" in result.lower() or "cannot" in result.lower()


async def test_webhook_receiver_handles_incoming():
    """The local webhook receiver should accept POST /incoming and queue it."""
    from mcp_server import _create_webhook_app, _incoming_queue

    # Clear the queue
    while not _incoming_queue.empty():
        _incoming_queue.get_nowait()

    webhook_app = _create_webhook_app()

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=webhook_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/incoming",
            json={
                "id": "msg-1",
                "from_name": "alice",
                "content": "pushed",
                "timestamp": "2026-04-08T12:00:00Z",
            },
        )
        assert resp.status_code == 200

    msg = _incoming_queue.get_nowait()
    assert msg["from_name"] == "alice"
    assert msg["content"] == "pushed"
