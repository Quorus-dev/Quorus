"""Integration tests for Postgres backends using testcontainers.

Run with: pytest tests/test_postgres_integration.py -v
Requires Docker to be running.

These tests verify the PostgresRoomHistoryBackend including:
- Message append and retrieval
- History trimming
- Search with filters
- Threading (reply_to)
- Room rename propagation
- Migration 001→005 on empty DB
"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration

try:
    from testcontainers.postgres import PostgresContainer

    TESTCONTAINERS_AVAILABLE = True
except ImportError:
    TESTCONTAINERS_AVAILABLE = False
    PostgresContainer = None  # type: ignore[misc,assignment]


@pytest.fixture(scope="module")
def postgres_container():
    """Start a Postgres container for the test module."""
    if not TESTCONTAINERS_AVAILABLE:
        pytest.skip("testcontainers not installed")
    try:
        with PostgresContainer("postgres:15-alpine") as container:
            yield container
    except PermissionError:
        pytest.skip("Docker socket not accessible (permission denied)")
    except Exception as e:
        if "docker" in str(e).lower() or "permission" in str(e).lower():
            pytest.skip(f"Docker unavailable: {e}")
        raise


@pytest.fixture(scope="module")
def database_url(postgres_container):
    """Get asyncpg connection URL from the container."""
    import re

    url = postgres_container.get_connection_url()
    # testcontainers may return postgresql://, postgresql+psycopg2://, etc.
    # Normalize to postgresql+asyncpg:// for SQLAlchemy async engine.
    return re.sub(r"^postgresql(\+\w+)?://", "postgresql+asyncpg://", url)


@pytest.fixture(scope="module")
def event_loop():
    """Create an event loop for the test module."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def migrated_db(database_url):
    """Run Alembic migrations on the test database."""
    import os

    from alembic import command
    from alembic.config import Config

    # Set DATABASE_URL for the migration
    os.environ["DATABASE_URL"] = database_url

    # Find alembic.ini relative to the murmur package
    import murmur

    murmur_dir = os.path.dirname(murmur.__file__)
    alembic_ini = os.path.join(murmur_dir, "..", "alembic.ini")

    if not os.path.exists(alembic_ini):
        pytest.skip(f"alembic.ini not found at {alembic_ini}")

    alembic_cfg = Config(alembic_ini)
    # Override script location to be relative to murmur package
    alembic_cfg.set_main_option(
        "script_location", os.path.join(murmur_dir, "migrations")
    )

    # Run migrations
    command.upgrade(alembic_cfg, "head")

    yield database_url

    # Cleanup handled by container teardown


@pytest.fixture
async def history_backend(migrated_db):
    """Create a PostgresRoomHistoryBackend connected to the test DB."""
    import os

    os.environ["DATABASE_URL"] = migrated_db

    from murmur.backends.postgres_history import PostgresRoomHistoryBackend

    return PostgresRoomHistoryBackend(max_history=10)


@pytest.fixture
def tenant_id():
    """Generate a unique tenant ID for test isolation."""
    return f"test-tenant-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def room_id():
    """Generate a unique room ID for test isolation."""
    return f"test-room-{uuid.uuid4().hex[:8]}"


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPostgresRoomHistory:
    """Tests for PostgresRoomHistoryBackend."""

    @pytest.mark.asyncio
    async def test_append_and_get_recent(self, history_backend, tenant_id, room_id):
        """Test basic message append and retrieval."""
        msg = {
            "id": str(uuid.uuid4()),
            "from_name": "alice",
            "content": "Hello, world!",
            "message_type": "chat",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "room": "test-room",
        }

        await history_backend.append(tenant_id, room_id, msg)

        messages = await history_backend.get_recent(tenant_id, room_id, limit=10)
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello, world!"
        assert messages[0]["from_name"] == "alice"
        assert messages[0]["room"] == "test-room"

    @pytest.mark.asyncio
    async def test_history_ordering(self, history_backend, tenant_id, room_id):
        """Test messages are returned in chronological order."""
        for i in range(5):
            msg = {
                "id": str(uuid.uuid4()),
                "from_name": "alice",
                "content": f"Message {i}",
                "message_type": "chat",
                "timestamp": f"2024-01-01T00:00:0{i}Z",
                "room": "test-room",
            }
            await history_backend.append(tenant_id, room_id, msg)

        messages = await history_backend.get_recent(tenant_id, room_id, limit=10)
        assert len(messages) == 5
        # Should be chronological (oldest to newest)
        for i, msg in enumerate(messages):
            assert msg["content"] == f"Message {i}"

    @pytest.mark.asyncio
    async def test_history_trim(self, history_backend, tenant_id, room_id):
        """Test history is trimmed to max_history."""
        # Backend has max_history=10, insert 15 messages
        for i in range(15):
            msg = {
                "id": str(uuid.uuid4()),
                "from_name": "alice",
                "content": f"Message {i}",
                "message_type": "chat",
                "timestamp": f"2024-01-01T00:00:{i:02d}Z",
                "room": "test-room",
            }
            await history_backend.append(tenant_id, room_id, msg)

        messages = await history_backend.get_recent(tenant_id, room_id, limit=20)
        # Should only have 10 messages (trimmed)
        assert len(messages) == 10
        # Should have messages 5-14 (oldest 5 trimmed)
        assert messages[0]["content"] == "Message 5"
        assert messages[-1]["content"] == "Message 14"

    @pytest.mark.asyncio
    async def test_search_by_content(self, history_backend, tenant_id, room_id):
        """Test search filters by content substring."""
        messages = [
            {"content": "Hello world", "from_name": "alice"},
            {"content": "Goodbye world", "from_name": "bob"},
            {"content": "Hello again", "from_name": "alice"},
        ]
        for i, m in enumerate(messages):
            await history_backend.append(
                tenant_id,
                room_id,
                {
                    "id": str(uuid.uuid4()),
                    "content": m["content"],
                    "from_name": m["from_name"],
                    "message_type": "chat",
                    "timestamp": f"2024-01-01T00:00:0{i}Z",
                    "room": "test-room",
                },
            )

        results = await history_backend.search(tenant_id, room_id, q="Hello")
        assert len(results) == 2
        assert all("Hello" in r["content"] for r in results)

    @pytest.mark.asyncio
    async def test_search_by_sender(self, history_backend, tenant_id, room_id):
        """Test search filters by sender."""
        messages = [
            {"content": "From alice", "from_name": "alice"},
            {"content": "From bob", "from_name": "bob"},
            {"content": "Also from alice", "from_name": "alice"},
        ]
        for i, m in enumerate(messages):
            await history_backend.append(
                tenant_id,
                room_id,
                {
                    "id": str(uuid.uuid4()),
                    "content": m["content"],
                    "from_name": m["from_name"],
                    "message_type": "chat",
                    "timestamp": f"2024-01-01T00:00:0{i}Z",
                    "room": "test-room",
                },
            )

        results = await history_backend.search(tenant_id, room_id, sender="alice")
        assert len(results) == 2
        assert all(r["from_name"] == "alice" for r in results)

    @pytest.mark.asyncio
    async def test_get_by_id(self, history_backend, tenant_id, room_id):
        """Test retrieving a specific message by ID."""
        msg_id = str(uuid.uuid4())
        await history_backend.append(
            tenant_id,
            room_id,
            {
                "id": msg_id,
                "content": "Find me!",
                "from_name": "alice",
                "message_type": "chat",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "room": "test-room",
            },
        )

        msg = await history_backend.get_by_id(tenant_id, room_id, msg_id)
        assert msg is not None
        assert msg["content"] == "Find me!"

        # Non-existent ID returns None
        missing = await history_backend.get_by_id(tenant_id, room_id, "no-such-id")
        assert missing is None

    @pytest.mark.asyncio
    async def test_threading(self, history_backend, tenant_id, room_id):
        """Test reply_to threading."""
        parent_id = str(uuid.uuid4())
        reply1_id = str(uuid.uuid4())
        reply2_id = str(uuid.uuid4())

        # Parent message
        await history_backend.append(
            tenant_id,
            room_id,
            {
                "id": parent_id,
                "content": "Parent",
                "from_name": "alice",
                "message_type": "chat",
                "timestamp": "2024-01-01T00:00:00Z",
                "room": "test-room",
            },
        )

        # Reply 1
        await history_backend.append(
            tenant_id,
            room_id,
            {
                "id": reply1_id,
                "content": "Reply 1",
                "from_name": "bob",
                "message_type": "chat",
                "timestamp": "2024-01-01T00:00:01Z",
                "reply_to": parent_id,
                "room": "test-room",
            },
        )

        # Reply 2
        await history_backend.append(
            tenant_id,
            room_id,
            {
                "id": reply2_id,
                "content": "Reply 2",
                "from_name": "alice",
                "message_type": "chat",
                "timestamp": "2024-01-01T00:00:02Z",
                "reply_to": parent_id,
                "room": "test-room",
            },
        )

        thread = await history_backend.get_thread(tenant_id, room_id, parent_id)
        assert len(thread) == 3
        assert thread[0]["content"] == "Parent"
        assert thread[1]["content"] == "Reply 1"
        assert thread[2]["content"] == "Reply 2"

    @pytest.mark.asyncio
    async def test_delete_room_history(self, history_backend, tenant_id, room_id):
        """Test deleting all messages in a room."""
        for i in range(3):
            await history_backend.append(
                tenant_id,
                room_id,
                {
                    "id": str(uuid.uuid4()),
                    "content": f"Message {i}",
                    "from_name": "alice",
                    "message_type": "chat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "room": "test-room",
                },
            )

        messages = await history_backend.get_recent(tenant_id, room_id, limit=10)
        assert len(messages) == 3

        await history_backend.delete(tenant_id, room_id)

        messages = await history_backend.get_recent(tenant_id, room_id, limit=10)
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_rename_room_in_history(self, history_backend, tenant_id, room_id):
        """Test updating denormalized room_name."""
        await history_backend.append(
            tenant_id,
            room_id,
            {
                "id": str(uuid.uuid4()),
                "content": "Before rename",
                "from_name": "alice",
                "message_type": "chat",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "room": "old-name",
            },
        )

        # Rename
        await history_backend.rename_room_in_history(tenant_id, room_id, "new-name")

        messages = await history_backend.get_recent(tenant_id, room_id, limit=10)
        assert len(messages) == 1
        assert messages[0]["room"] == "new-name"


class TestMigrations:
    """Test Alembic migrations run successfully."""

    @pytest.mark.asyncio
    async def test_migrations_applied(self, migrated_db):
        """Verify all migrations were applied."""
        import os

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        os.environ["DATABASE_URL"] = migrated_db
        engine = create_async_engine(migrated_db)

        async with engine.connect() as conn:
            # Check alembic_version table exists and has a version
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = result.scalar()
            # Should be at head (005_add_room_name)
            assert version is not None
            assert "005" in version or version.startswith("005")

            # Check messages table has expected columns
            result = await conn.execute(
                text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'messages'
                """)
            )
            columns = {row[0] for row in result.fetchall()}
            expected = {
                "id",
                "tenant_id",
                "from_name",
                "to_name",
                "room_id",
                "room_name",
                "content",
                "message_type",
                "timestamp",
                "reply_to",
            }
            assert expected.issubset(columns)

        await engine.dispose()
