"""Tests for SQLiteRoomHistoryBackend."""

from __future__ import annotations

import pytest

from quorus.backends.sqlite_history import SQLiteRoomHistoryBackend


@pytest.fixture
def history(tmp_path):
    db_path = str(tmp_path / "test_history.db")
    backend = SQLiteRoomHistoryBackend(db_path=db_path, max_history=10)
    yield backend
    backend.close()


def _msg(i: int, room: str = "dev", mtype: str = "chat", reply_to=None) -> dict:
    return {
        "id": f"msg-{i}",
        "from": f"agent-{i % 3}",
        "from_name": f"agent-{i % 3}",
        "room": room,
        "content": f"message content {i}",
        "message_type": mtype,
        "timestamp": f"2026-04-12T00:0{i}:00+00:00",
        "reply_to": reply_to,
    }


TENANT = "t1"
ROOM = "r1"


class TestAppendAndGetRecent:
    async def test_empty_room_returns_empty(self, history):
        result = await history.get_recent(TENANT, ROOM, limit=10)
        assert result == []

    async def test_single_message_persists(self, history):
        msg = _msg(1)
        await history.append(TENANT, ROOM, msg)
        result = await history.get_recent(TENANT, ROOM, limit=10)
        assert len(result) == 1
        assert result[0]["id"] == "msg-1"
        assert result[0]["content"] == "message content 1"

    async def test_messages_returned_in_chronological_order(self, history):
        for i in range(1, 6):
            await history.append(TENANT, ROOM, _msg(i))
        result = await history.get_recent(TENANT, ROOM, limit=10)
        ids = [m["id"] for m in result]
        assert ids == ["msg-1", "msg-2", "msg-3", "msg-4", "msg-5"]

    async def test_limit_is_respected(self, history):
        for i in range(1, 6):
            await history.append(TENANT, ROOM, _msg(i))
        result = await history.get_recent(TENANT, ROOM, limit=3)
        assert len(result) == 3
        assert result[0]["id"] == "msg-3"
        assert result[-1]["id"] == "msg-5"

    async def test_history_is_tenant_scoped(self, history):
        await history.append("tenant-a", ROOM, _msg(1))
        await history.append("tenant-b", ROOM, _msg(2))
        a = await history.get_recent("tenant-a", ROOM, limit=10)
        b = await history.get_recent("tenant-b", ROOM, limit=10)
        assert len(a) == 1 and a[0]["id"] == "msg-1"
        assert len(b) == 1 and b[0]["id"] == "msg-2"

    async def test_history_is_room_scoped(self, history):
        await history.append(TENANT, "room-a", _msg(1))
        await history.append(TENANT, "room-b", _msg(2))
        a = await history.get_recent(TENANT, "room-a", limit=10)
        b = await history.get_recent(TENANT, "room-b", limit=10)
        assert len(a) == 1 and a[0]["id"] == "msg-1"
        assert len(b) == 1 and b[0]["id"] == "msg-2"

    async def test_max_history_trims_oldest(self, history):
        # max_history is 10; write 15 messages
        for i in range(1, 16):
            await history.append(TENANT, ROOM, _msg(i))
        result = await history.get_recent(TENANT, ROOM, limit=50)
        assert len(result) == 10
        ids = [m["id"] for m in result]
        # Should keep the 10 newest
        assert "msg-1" not in ids
        assert "msg-15" in ids

    async def test_duplicate_id_ignored(self, history):
        msg = _msg(1)
        await history.append(TENANT, ROOM, msg)
        await history.append(TENANT, ROOM, msg)  # same id
        result = await history.get_recent(TENANT, ROOM, limit=10)
        assert len(result) == 1


class TestGetById:
    async def test_found(self, history):
        await history.append(TENANT, ROOM, _msg(1))
        result = await history.get_by_id(TENANT, ROOM, "msg-1")
        assert result is not None
        assert result["id"] == "msg-1"

    async def test_not_found_returns_none(self, history):
        result = await history.get_by_id(TENANT, ROOM, "does-not-exist")
        assert result is None

    async def test_scoped_to_room(self, history):
        await history.append(TENANT, "room-a", _msg(1))
        result = await history.get_by_id(TENANT, "room-b", "msg-1")
        assert result is None


class TestGetThread:
    async def test_returns_parent_and_replies(self, history):
        parent = _msg(1)
        reply1 = _msg(2, reply_to="msg-1")
        reply2 = _msg(3, reply_to="msg-1")
        unrelated = _msg(4)
        for m in [parent, reply1, reply2, unrelated]:
            await history.append(TENANT, ROOM, m)

        thread = await history.get_thread(TENANT, ROOM, "msg-1")
        ids = [m["id"] for m in thread]
        assert "msg-1" in ids
        assert "msg-2" in ids
        assert "msg-3" in ids
        assert "msg-4" not in ids

    async def test_missing_parent_returns_only_replies(self, history):
        reply = _msg(2, reply_to="ghost-parent")
        await history.append(TENANT, ROOM, reply)
        thread = await history.get_thread(TENANT, ROOM, "ghost-parent")
        assert len(thread) == 1
        assert thread[0]["id"] == "msg-2"

    async def test_no_replies_returns_parent_only(self, history):
        await history.append(TENANT, ROOM, _msg(1))
        thread = await history.get_thread(TENANT, ROOM, "msg-1")
        assert len(thread) == 1
        assert thread[0]["id"] == "msg-1"


class TestSearch:
    async def test_search_by_content(self, history):
        await history.append(TENANT, ROOM, {**_msg(1), "content": "hello world"})
        await history.append(TENANT, ROOM, {**_msg(2), "content": "goodbye world"})
        await history.append(TENANT, ROOM, {**_msg(3), "content": "hello there"})

        results = await history.search(TENANT, ROOM, q="hello")
        ids = [m["id"] for m in results]
        assert "msg-1" in ids
        assert "msg-3" in ids
        assert "msg-2" not in ids

    async def test_search_by_sender(self, history):
        await history.append(TENANT, ROOM, {**_msg(1), "from_name": "alice", "from": "alice"})
        await history.append(TENANT, ROOM, {**_msg(2), "from_name": "bob", "from": "bob"})

        results = await history.search(TENANT, ROOM, sender="alice")
        assert len(results) == 1
        assert results[0]["id"] == "msg-1"

    async def test_search_by_message_type(self, history):
        await history.append(TENANT, ROOM, _msg(1, mtype="chat"))
        await history.append(TENANT, ROOM, _msg(2, mtype="decision"))
        await history.append(TENANT, ROOM, _msg(3, mtype="decision"))

        results = await history.search(TENANT, ROOM, message_type="decision")
        assert len(results) == 2

    async def test_search_limit(self, history):
        for i in range(1, 8):
            await history.append(TENANT, ROOM, {**_msg(i), "content": "match"})
        results = await history.search(TENANT, ROOM, q="match", limit=3)
        assert len(results) == 3

    async def test_empty_search_returns_recent(self, history):
        for i in range(1, 4):
            await history.append(TENANT, ROOM, _msg(i))
        results = await history.search(TENANT, ROOM)
        assert len(results) == 3


class TestDelete:
    async def test_delete_clears_room_history(self, history):
        for i in range(1, 4):
            await history.append(TENANT, ROOM, _msg(i))
        await history.delete(TENANT, ROOM)
        result = await history.get_recent(TENANT, ROOM, limit=10)
        assert result == []

    async def test_delete_is_room_scoped(self, history):
        await history.append(TENANT, "room-a", _msg(1))
        await history.append(TENANT, "room-b", _msg(2))
        await history.delete(TENANT, "room-a")
        assert await history.get_recent(TENANT, "room-a", limit=10) == []
        assert len(await history.get_recent(TENANT, "room-b", limit=10)) == 1


class TestRenameRoom:
    async def test_rename_updates_room_name_in_history(self, history):
        await history.append(TENANT, ROOM, {**_msg(1), "room": "old-name"})
        await history.rename_room_in_history(TENANT, ROOM, "new-name")
        result = await history.get_recent(TENANT, ROOM, limit=10)
        assert result[0]["room"] == "new-name"


class TestPersistence:
    async def test_history_survives_reconnect(self, tmp_path):
        """Closing and reopening the DB should retain all messages."""
        db_path = str(tmp_path / "persist.db")
        b1 = SQLiteRoomHistoryBackend(db_path=db_path)
        await b1.append(TENANT, ROOM, _msg(1))
        await b1.append(TENANT, ROOM, _msg(2))
        b1.close()

        b2 = SQLiteRoomHistoryBackend(db_path=db_path)
        result = await b2.get_recent(TENANT, ROOM, limit=10)
        b2.close()

        assert len(result) == 2
        assert result[0]["id"] == "msg-1"
        assert result[1]["id"] == "msg-2"
