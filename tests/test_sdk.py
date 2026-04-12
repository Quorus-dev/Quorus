"""Tests for murmur/sdk.py — high-level Room SDK."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from murmur.relay import _reset_state, app
from murmur.sdk import Room


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


@pytest.fixture
async def setup_room(client, auth_headers):
    """Create a room and join an agent for SDK tests."""
    await client.post(
        "/rooms",
        json={"name": "sdk-test", "created_by": "tester"},
        headers=auth_headers,
    )
    await client.post(
        "/rooms/sdk-test/join",
        json={"participant": "sdk-agent"},
        headers=auth_headers,
    )


class TestRoomInit:
    def test_room_has_room_attr(self):
        """Room should expose room name."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp
            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "test", relay="http://t", secret="s",
                    name="agent-1",
                )
                assert room.room == "test"
                assert room.name == "agent-1"

    def test_room_stores_config(self):
        """Room should store relay, secret, name."""
        with patch.object(
            Room, 'join', return_value={}
        ):
            room = Room(
                "my-room",
                relay="http://localhost:9999",
                secret="s3cret",
                name="my-agent",
            )
            assert room.room == "my-room"
            assert room.name == "my-agent"
            assert room.relay == "http://localhost:9999"
            assert room.secret == "s3cret"


class TestRoomSendReceive:
    @pytest.mark.asyncio
    async def test_send_message(self, client, auth_headers, setup_room):
        """Room.send should post a message to the room."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "id": "123",
                "timestamp": "2026-04-11T00:00:00Z",
            }
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "sdk-test",
                    relay="http://test",
                    secret="test-secret",
                    name="sdk-agent",
                )
                result = room.send("hello world")

            assert result["id"] == "123"
            call_args = mock_req.call_args
            assert call_args[0][0] == "POST"
            assert "sdk-test/messages" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_send_with_type(self, client, auth_headers, setup_room):
        """Room.send should pass message_type."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": "456"}
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "sdk-test",
                    relay="http://test",
                    secret="test-secret",
                    name="sdk-agent",
                )
                room.send("task X", type="claim")

            body = mock_req.call_args[1]["json"]
            assert body["message_type"] == "claim"

    @pytest.mark.asyncio
    async def test_receive_messages(self):
        """Room.receive should return ReceiveResult with messages."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = [
                {"content": "msg1", "from_name": "a"},
            ]
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "test",
                    relay="http://test",
                    secret="s",
                    name="agent",
                )
                result = room.receive()

            assert len(result.messages) == 1
            assert result.messages[0]["content"] == "msg1"


class TestRoomConvenience:
    def test_claim(self):
        """Room.claim should send a CLAIM message."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": "c1"}
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "test", relay="http://t", secret="s", name="a",
                )
                room.claim("auth module")

            body = mock_req.call_args[1]["json"]
            assert "CLAIM: auth module" in body["content"]
            assert body["message_type"] == "claim"

    def test_status(self):
        """Room.status should send a STATUS message."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": "s1"}
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "test", relay="http://t", secret="s", name="a",
                )
                room.status("done")

            body = mock_req.call_args[1]["json"]
            assert "STATUS: done" in body["content"]
            assert body["message_type"] == "status"

    def test_alert(self):
        """Room.alert should send an ALERT message."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": "a1"}
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "test", relay="http://t", secret="s", name="a",
                )
                room.alert("fire")

            body = mock_req.call_args[1]["json"]
            assert "ALERT: fire" in body["content"]
            assert body["message_type"] == "alert"

    def test_history(self):
        """Room.history should fetch room history."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = [{"content": "old"}]
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "test", relay="http://t", secret="s", name="a",
                )
                msgs = room.history(limit=10)

            assert len(msgs) == 1
            assert "history" in mock_req.call_args[0][1]

    def test_members(self):
        """Room.members should return member list."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = [
                {"name": "test", "members": ["a", "b"]},
            ]
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "test", relay="http://t", secret="s", name="a",
                )
                m = room.members()

            assert m == ["a", "b"]

    def test_peek(self):
        """Room.peek should return pending count."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"count": 5}
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                room = Room(
                    "test", relay="http://t", secret="s", name="a",
                )
                assert room.peek() == 5


class TestRoomContextManager:
    def test_sync_context_manager(self):
        """Room should work as sync context manager."""
        with patch("murmur.sdk.httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            mock_resp.raise_for_status = MagicMock()
            mock_req.return_value = mock_resp

            with patch.object(Room, 'join', return_value={}):
                with Room(
                    "test", relay="http://t", secret="s", name="a",
                ) as room:
                    assert room.room == "test"


class TestRoomListener:
    def test_on_message_registers_callback(self):
        """on_message should add callback to listeners."""
        with patch.object(Room, 'join', return_value={}):
            room = Room(
                "test", relay="http://t", secret="s", name="a",
            )
            cb = lambda msg: None  # noqa: E731
            room.on_message(cb)
            assert cb in room._listeners

    def test_listen_async_starts_thread(self):
        """listen_async should start a daemon thread."""
        with patch.object(Room, 'join', return_value={}):
            room = Room(
                "test", relay="http://t", secret="s", name="a",
            )
            room.on_message(lambda m: None)
            with patch.object(room, 'receive', return_value=[]):
                room.listen_async(poll_interval=1)
                assert room._stream_task is not None
                assert room._stream_task.is_alive()
                room.stop()
                assert not room._stream_task

    def test_stop_stops_listener(self):
        """stop should terminate the polling thread."""
        with patch.object(Room, 'join', return_value={}):
            room = Room(
                "test", relay="http://t", secret="s", name="a",
            )
            room.on_message(lambda m: None)
            with patch.object(room, 'receive', return_value=[]):
                room.listen_async(poll_interval=1)
                room.stop()
                assert room._stop_event.is_set()
