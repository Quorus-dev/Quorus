from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def configure_cli(monkeypatch):
    monkeypatch.setattr("cli.RELAY_URL", "http://test-relay:8080")
    monkeypatch.setattr("cli.RELAY_SECRET", "test-secret")
    monkeypatch.setattr("cli.INSTANCE_NAME", "test-user")


def _mock_response(status_code, json_data):
    """Create a sync MagicMock mimicking httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _mock_client(status_code, json_data):
    resp = _mock_response(status_code, json_data)
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    return client


async def test_cli_list_rooms():
    from cli import _list_rooms

    mock_rooms = [
        {
            "id": "r1",
            "name": "yc-hack",
            "members": ["alice", "bob"],
            "created_at": "2026-04-11T00:00:00Z",
        }
    ]
    with patch("cli._get_client", return_value=_mock_client(200, mock_rooms)):
        result = await _list_rooms()
    assert len(result) == 1
    assert result[0]["name"] == "yc-hack"


async def test_cli_create_room():
    from cli import _create_room

    mock_data = {
        "id": "r1",
        "name": "new-room",
        "members": ["test-user"],
        "created_at": "2026-04-11T00:00:00Z",
    }
    with patch("cli._get_client", return_value=_mock_client(200, mock_data)):
        result = await _create_room("new-room")
    assert result["name"] == "new-room"


async def test_cli_say():
    from cli import _say

    rooms_data = [{"id": "r1", "name": "yc-hack", "members": ["test-user"]}]
    msg_data = {"id": "m1", "timestamp": "2026-04-11T00:00:00Z"}

    mock_resp_rooms = _mock_response(200, rooms_data)
    mock_resp_msg = _mock_response(200, msg_data)

    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_resp_rooms)
    client.post = AsyncMock(return_value=mock_resp_msg)
    client.aclose = AsyncMock()

    with patch("cli._get_client", return_value=client):
        result = await _say("yc-hack", "hello team")
    assert result["id"] == "m1"


async def test_cli_dm():
    from cli import _dm

    mock_data = {"id": "m1", "timestamp": "2026-04-11T00:00:00Z"}
    with patch("cli._get_client", return_value=_mock_client(200, mock_data)):
        result = await _dm("bob", "private message")
    assert result["id"] == "m1"
