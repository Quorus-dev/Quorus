"""Integration tests for Redis backends using testcontainers.

Run with: pytest tests/test_redis_integration.py -v
Requires Docker to be running.

These tests verify the actual Redis behavior including:
- Stream consumer groups (XREADGROUP, XACK, XDEL)
- Visibility timeout and XAUTOCLAIM
- Atomic requeue Lua script
- Chunked message handling
"""

import asyncio
import json

import pytest
from redis.asyncio import Redis

from murmur.backends.redis_backends import (
    RedisIdempotencyBackend,
    RedisMessageBackend,
    RedisWebhookQueueBackend,
)

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration

try:
    from testcontainers.redis import RedisContainer

    TESTCONTAINERS_AVAILABLE = True
except ImportError:
    TESTCONTAINERS_AVAILABLE = False
    RedisContainer = None  # type: ignore[misc,assignment]


@pytest.fixture(scope="module")
def redis_container():
    """Start a Redis container for the test module."""
    if not TESTCONTAINERS_AVAILABLE:
        pytest.skip("testcontainers not installed")
    try:
        with RedisContainer("redis:7-alpine") as container:
            yield container
    except PermissionError:
        pytest.skip("Docker socket not accessible (permission denied)")
    except Exception as e:
        if "docker" in str(e).lower() or "permission" in str(e).lower():
            pytest.skip(f"Docker unavailable: {e}")
        raise


@pytest.fixture
async def redis_client(redis_container):
    """Create an async Redis client connected to the test container."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = Redis(host=host, port=int(port), decode_responses=True)
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def message_backend(redis_client):
    """Create a RedisMessageBackend for testing."""
    return RedisMessageBackend(redis_client)


class TestRedisMessageBackend:
    """Test Redis Streams-based message delivery."""

    async def test_enqueue_and_fetch(self, message_backend):
        """Basic enqueue and fetch should work."""
        msg = {"id": "msg-1", "from_name": "alice", "content": "hello"}
        await message_backend.enqueue("tenant1", "bob", msg)

        messages, ack_token = await message_backend.fetch("tenant1", "bob")
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"
        assert messages[0]["_delivery_id"]  # Should have delivery ID
        assert ack_token

    async def test_ack_removes_message(self, message_backend):
        """ACKed messages should not be redelivered."""
        msg = {"id": "msg-1", "from_name": "alice", "content": "hello"}
        await message_backend.enqueue("tenant1", "bob", msg)

        messages, ack_token = await message_backend.fetch("tenant1", "bob")
        assert len(messages) == 1

        # ACK the message
        await message_backend.ack("tenant1", "bob", ack_token)

        # Should not get any messages on next fetch
        messages2, _ = await message_backend.fetch("tenant1", "bob")
        assert len(messages2) == 0

    async def test_unacked_message_stays_pending(self, message_backend):
        """Unacked messages should stay in pending state."""
        msg = {"id": "msg-1", "from_name": "alice", "content": "hello"}
        await message_backend.enqueue("tenant1", "bob", msg)

        # Fetch but don't ACK
        messages, _ = await message_backend.fetch("tenant1", "bob")
        assert len(messages) == 1

        # Immediate refetch should return empty (message is pending)
        messages2, _ = await message_backend.fetch("tenant1", "bob")
        assert len(messages2) == 0

        # But pending count should show 1
        pending = await message_backend.pending_count("tenant1", "bob")
        assert pending == 1

    async def test_ack_by_ids(self, message_backend):
        """Per-message ACK should work."""
        msg1 = {"id": "msg-1", "from_name": "alice", "content": "hello"}
        msg2 = {"id": "msg-2", "from_name": "alice", "content": "world"}
        await message_backend.enqueue("tenant1", "bob", msg1)
        await message_backend.enqueue("tenant1", "bob", msg2)

        messages, _ = await message_backend.fetch("tenant1", "bob")
        assert len(messages) == 2

        # ACK only the first message
        delivery_id = messages[0]["_delivery_id"]
        acked = await message_backend.ack_ids("tenant1", "bob", [delivery_id])
        assert acked == 1

        # Should have 1 pending
        pending = await message_backend.pending_count("tenant1", "bob")
        assert pending == 1

    async def test_requeue_atomic(self, message_backend):
        """Atomic requeue should ACK old entries and add new ones."""
        msg = {"id": "msg-1", "from_name": "alice", "content": "hello"}
        await message_backend.enqueue("tenant1", "bob", msg)

        messages, _ = await message_backend.fetch("tenant1", "bob")
        assert len(messages) == 1
        old_delivery_id = messages[0]["_delivery_id"]

        # Requeue the message (simulating incomplete chunk handling)
        new_msg = {"id": "msg-1", "from_name": "alice", "content": "hello"}
        await message_backend.requeue(
            "tenant1", "bob", [old_delivery_id], [new_msg]
        )

        # Should be able to fetch the new entry
        messages2, _ = await message_backend.fetch("tenant1", "bob")
        assert len(messages2) == 1
        assert messages2[0]["_delivery_id"] != old_delivery_id  # New ID

        # Old entry should be gone (ACKed)
        pending = await message_backend.pending_count("tenant1", "bob")
        assert pending == 1  # Only the new one

    async def test_batch_enqueue(self, message_backend):
        """Batch enqueue should add multiple messages atomically."""
        messages = [
            {"id": f"msg-{i}", "from_name": "alice", "content": f"hello {i}"}
            for i in range(5)
        ]
        await message_backend.enqueue_batch("tenant1", "bob", messages)

        fetched, _ = await message_backend.fetch("tenant1", "bob")
        assert len(fetched) == 5


class TestRedisChunkedMessages:
    """Test chunked message handling with Redis."""

    async def test_chunked_message_delivery_ids(self, message_backend):
        """Each chunk should get its own delivery ID."""
        chunks = [
            {
                "id": f"chunk-{i}",
                "from_name": "alice",
                "content": f"part{i}",
                "chunk_group": "group-1",
                "chunk_index": i,
                "chunk_total": 3,
            }
            for i in range(3)
        ]
        await message_backend.enqueue_batch("tenant1", "bob", chunks)

        messages, ack_token = await message_backend.fetch("tenant1", "bob")
        assert len(messages) == 3

        # Each chunk should have a unique delivery ID
        delivery_ids = [m["_delivery_id"] for m in messages]
        assert len(set(delivery_ids)) == 3

        # ACK token should contain all 3 IDs
        token_ids = json.loads(ack_token)
        assert len(token_ids) == 3


class TestRedisIdempotency:
    """Test idempotency backend with Redis."""

    async def test_idempotency_set_and_get(self, redis_client):
        """Basic idempotency flow should work."""

        backend = RedisIdempotencyBackend(redis_client)

        # First set
        await backend.set("tenant1", "key-1", {"id": "msg-1"}, ttl=60)

        # Should be able to get
        result = await backend.get("tenant1", "key-1")
        assert result == {"id": "msg-1"}

    async def test_idempotency_ttl_expiry(self, redis_client):
        """Idempotency keys should expire after TTL."""
        backend = RedisIdempotencyBackend(redis_client)

        # Set with 1 second TTL
        await backend.set("tenant1", "key-expire", {"id": "msg-1"}, ttl=1)

        # Should exist immediately
        result = await backend.get("tenant1", "key-expire")
        assert result is not None

        # Wait for expiry
        await asyncio.sleep(1.1)

        # Should be gone
        result = await backend.get("tenant1", "key-expire")
        assert result is None


class TestRedisWebhookQueue:
    """Test durable webhook queue with Redis Streams."""

    async def test_enqueue_and_fetch(self, redis_client):
        """Basic enqueue and fetch should work."""
        backend = RedisWebhookQueueBackend(redis_client)

        job = {
            "target": "test-target",
            "callback_url": "https://example.com/hook",
            "payload": {"message": "hello"},
            "secret": "",
            "attempt": 0,
            "created_at": 1234567890.0,
            "last_error": "",
        }
        job_id = await backend.enqueue(job)
        assert job_id

        jobs = await backend.fetch(count=10)
        assert len(jobs) == 1
        fetched_id, fetched_job = jobs[0]
        assert fetched_id == job_id
        assert fetched_job["target"] == "test-target"
        assert fetched_job["callback_url"] == "https://example.com/hook"

    async def test_ack_removes_job(self, redis_client):
        """ACKed jobs should be removed from the queue."""
        backend = RedisWebhookQueueBackend(redis_client)

        job = {"target": "ack-test", "callback_url": "https://example.com/hook",
               "payload": {}, "secret": "", "attempt": 0, "created_at": 0.0,
               "last_error": ""}
        job_id = await backend.enqueue(job)

        jobs = await backend.fetch(count=10)
        assert len(jobs) == 1

        await backend.ack(job_id)

        # Should not get the job again
        jobs2 = await backend.fetch(count=10)
        assert len(jobs2) == 0

    async def test_nack_retries_job(self, redis_client):
        """NACKed jobs should be re-enqueued for retry."""
        backend = RedisWebhookQueueBackend(redis_client)

        job = {"target": "nack-test", "callback_url": "https://example.com/hook",
               "payload": {}, "secret": "", "attempt": 0, "created_at": 0.0,
               "last_error": ""}
        await backend.enqueue(job)

        jobs = await backend.fetch(count=10)
        assert len(jobs) == 1
        original_id, _ = jobs[0]

        # NACK with room for retry
        will_retry = await backend.nack(original_id, "connection failed", max_retries=3)
        assert will_retry is True

        # Should get the job again (re-enqueued)
        jobs2 = await backend.fetch(count=10)
        assert len(jobs2) == 1
        new_id, new_job = jobs2[0]
        assert new_id != original_id  # New entry
        assert new_job["attempt"] == 1
        assert new_job["last_error"] == "connection failed"

    async def test_nack_moves_to_dlq_after_max_retries(self, redis_client):
        """Jobs should move to DLQ after max retries."""
        backend = RedisWebhookQueueBackend(redis_client)

        job = {"target": "dlq-test", "callback_url": "https://example.com/hook",
               "payload": {}, "secret": "", "attempt": 2, "created_at": 0.0,
               "last_error": ""}
        job_id = await backend.enqueue(job)

        jobs = await backend.fetch(count=10)
        assert len(jobs) == 1

        # NACK with attempt already at max-1
        will_retry = await backend.nack(job_id, "final failure", max_retries=3)
        assert will_retry is False

        # Job should not be re-enqueued
        jobs2 = await backend.fetch(count=10)
        assert len(jobs2) == 0

        # Job should be in DLQ
        dlq = await backend.get_dlq(limit=10)
        assert len(dlq) == 1
        assert dlq[0]["target"] == "dlq-test"
        assert dlq[0]["attempt"] == 3
        assert dlq[0]["last_error"] == "final failure"

    async def test_get_stats(self, redis_client):
        """Stats should reflect queue state."""
        backend = RedisWebhookQueueBackend(redis_client)

        stats = await backend.get_stats()
        assert stats["queue_length"] == 0
        assert stats["dlq_size"] == 0

        # Enqueue a job
        job = {"target": "stats-test", "callback_url": "https://example.com/hook",
               "payload": {}, "secret": "", "attempt": 0, "created_at": 0.0,
               "last_error": ""}
        await backend.enqueue(job)

        stats = await backend.get_stats()
        assert stats["queue_length"] == 1
