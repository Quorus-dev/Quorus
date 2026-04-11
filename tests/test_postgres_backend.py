"""Tests for PostgresQueueBackend — mocked DB session, no real Postgres needed."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from murmur.storage.postgres_backend import PostgresQueueBackend

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TENANT = "tenant-1"
RECIPIENT = "agent-b"


def _make_msg(
    msg_id: str = "msg-1",
    from_name: str = "agent-a",
    content: str = "hello",
    **extra,
) -> dict:
    return {
        "id": msg_id,
        "from_name": from_name,
        "to": RECIPIENT,
        "content": content,
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        **extra,
    }


class _FakeScalarsResult:
    """Mimics the chained result.scalars().all() pattern."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    """Mimics SQLAlchemy async Result with .scalars() and .scalar_one()."""

    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar_value = scalar_value

    def scalars(self):
        return _FakeScalarsResult(self._rows)

    def scalar_one(self):
        return self._scalar_value


@pytest.fixture
def backend():
    return PostgresQueueBackend()


@pytest.fixture
def mock_session():
    """Return a mock AsyncSession that tracks add() calls and execute() calls."""
    session = AsyncMock()
    session.add = MagicMock()  # synchronous in SQLAlchemy
    session.execute = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_adds_message(self, backend, mock_session):
        msg = _make_msg()

        # Patch get_db_session to yield our mock session
        with _patched_session(mock_session):
            await backend.enqueue(TENANT, RECIPIENT, msg)

        # session.add() should have been called with a Message ORM object
        assert mock_session.add.call_count == 1
        orm_obj = mock_session.add.call_args[0][0]
        assert orm_obj.id == "msg-1"
        assert orm_obj.tenant_id == TENANT
        assert orm_obj.from_name == "agent-a"
        assert orm_obj.to_name == RECIPIENT
        assert orm_obj.content == "hello"
        assert orm_obj.message_type == "chat"

    @pytest.mark.asyncio
    async def test_enqueue_maps_optional_fields(self, backend, mock_session):
        msg = _make_msg(
            room="room-x",
            chunk_group="cg-1",
            chunk_index=0,
            chunk_total=3,
            message_type="system",
        )

        with _patched_session(mock_session):
            await backend.enqueue(TENANT, RECIPIENT, msg)

        orm_obj = mock_session.add.call_args[0][0]
        assert orm_obj.room_id == "room-x"
        assert orm_obj.chunk_group == "cg-1"
        assert orm_obj.chunk_index == 0
        assert orm_obj.chunk_total == 3
        assert orm_obj.message_type == "system"


class TestDequeueAll:
    @pytest.mark.asyncio
    async def test_dequeue_returns_messages_and_marks_delivered(
        self, backend, mock_session
    ):
        fake_row = MagicMock()
        fake_row.id = "msg-1"
        fake_row.from_name = "agent-a"
        fake_row.to_name = RECIPIENT
        fake_row.content = "hello"
        fake_row.timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        fake_row.room_id = None
        fake_row.chunk_group = None
        fake_row.chunk_index = None
        fake_row.chunk_total = None
        fake_row.message_type = "chat"

        # First execute = SELECT, second execute = UPDATE
        mock_session.execute = AsyncMock(
            side_effect=[
                _FakeResult(rows=[fake_row]),
                _FakeResult(),  # UPDATE returns no rows
            ]
        )

        with _patched_session(mock_session):
            result = await backend.dequeue_all(TENANT, RECIPIENT)

        assert len(result) == 1
        assert result[0]["id"] == "msg-1"
        assert result[0]["from_name"] == "agent-a"
        assert result[0]["to"] == RECIPIENT
        assert result[0]["content"] == "hello"

        # Two execute calls: SELECT then UPDATE
        assert mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_dequeue_empty_queue_returns_empty(self, backend, mock_session):
        mock_session.execute = AsyncMock(return_value=_FakeResult(rows=[]))

        with _patched_session(mock_session):
            result = await backend.dequeue_all(TENANT, RECIPIENT)

        assert result == []
        # Only the SELECT, no UPDATE needed
        assert mock_session.execute.call_count == 1


class TestPeek:
    @pytest.mark.asyncio
    async def test_peek_returns_count(self, backend, mock_session):
        mock_session.execute = AsyncMock(
            return_value=_FakeResult(scalar_value=5)
        )

        with _patched_session(mock_session):
            count = await backend.peek(TENANT, RECIPIENT)

        assert count == 5
        assert mock_session.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_peek_zero_when_empty(self, backend, mock_session):
        mock_session.execute = AsyncMock(
            return_value=_FakeResult(scalar_value=0)
        )

        with _patched_session(mock_session):
            count = await backend.peek(TENANT, RECIPIENT)

        assert count == 0


class TestEnqueueBatch:
    @pytest.mark.asyncio
    async def test_batch_inserts_multiple(self, backend, mock_session):
        msgs = [
            _make_msg(msg_id="msg-1", content="part 1"),
            _make_msg(msg_id="msg-2", content="part 2"),
            _make_msg(msg_id="msg-3", content="part 3"),
        ]

        with _patched_session(mock_session):
            await backend.enqueue_batch(TENANT, RECIPIENT, msgs)

        assert mock_session.add.call_count == 3
        added_ids = [
            mock_session.add.call_args_list[i][0][0].id for i in range(3)
        ]
        assert added_ids == ["msg-1", "msg-2", "msg-3"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _patched_session(mock_session):
    """Return a patch context that replaces get_db_session with a mock."""

    @asynccontextmanager
    async def _fake_get_db_session():
        yield mock_session

    return patch(
        "murmur.storage.postgres_backend.get_db_session",
        _fake_get_db_session,
    )
