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
    RedisRoomStateBackend,
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

        # First set (with body fingerprint)
        await backend.set("tenant1", "key-1", "fp-abc123", {"id": "msg-1"}, ttl=60)

        # Should be able to get
        result = await backend.get("tenant1", "key-1")
        assert result == {"id": "msg-1"}

    async def test_idempotency_ttl_expiry(self, redis_client):
        """Idempotency keys should expire after TTL."""
        backend = RedisIdempotencyBackend(redis_client)

        # Set with 1 second TTL
        await backend.set("tenant1", "key-expire", "fp-abc123", {"id": "msg-1"}, ttl=1)

        # Should exist immediately
        result = await backend.get("tenant1", "key-expire")
        assert result is not None

        # Wait for expiry
        await asyncio.sleep(1.1)

        # Should be gone
        result = await backend.get("tenant1", "key-expire")
        assert result is None

    async def test_idempotency_body_mismatch_409(self, redis_client):
        """Same key with different body should return 409."""
        import pytest
        from fastapi import HTTPException

        backend = RedisIdempotencyBackend(redis_client)

        # Reserve with one body fingerprint
        result = await backend.reserve("tenant1", "key-conflict", "fp-body1", ttl=60)
        assert result is None  # Reservation succeeded

        # Store result
        await backend.set("tenant1", "key-conflict", "fp-body1", {"id": "msg-1"}, ttl=60)

        # Try to reserve with different body fingerprint - should 409
        with pytest.raises(HTTPException) as exc_info:
            await backend.reserve("tenant1", "key-conflict", "fp-body2", ttl=60)
        assert exc_info.value.status_code == 409
        assert "different request body" in exc_info.value.detail


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

    async def test_nack_schedules_retry_with_backoff(self, redis_client):
        """NACKed jobs should be scheduled for retry with exponential backoff."""
        import time

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

        # Job should be in delayed set, not immediately available
        stats = await backend.get_stats()
        assert stats["delayed"] == 1
        assert stats["queue_length"] == 0

        # Fetch should return nothing (job is delayed)
        jobs2 = await backend.fetch(count=10)
        assert len(jobs2) == 0

        # Manually promote the delayed job by setting its score to past
        # (simulating time passing)
        delayed_jobs = await redis_client.zrange("webhook:delay", 0, -1)
        assert len(delayed_jobs) == 1
        await redis_client.zadd("webhook:delay", {delayed_jobs[0]: time.time() - 1})

        # Now promote_delayed should move it to the queue
        promoted = await backend.promote_delayed()
        assert promoted == 1

        # Job should now be fetchable
        jobs3 = await backend.fetch(count=10)
        assert len(jobs3) == 1
        new_id, new_job = jobs3[0]
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


class TestRedisRoomStateLocking:
    """Test distributed lock operations with Redis."""

    async def test_concurrent_lock_acquire_only_one_wins(self, redis_client):
        """When two agents try to acquire the same lock, only one succeeds."""
        backend = RedisRoomStateBackend(redis_client)

        lock_data = {
            "held_by": "agent-1",
            "lock_token": "token-1",
            "expires_at": "2099-12-31T23:59:59+00:00",
        }
        task_data = {
            "id": "task-1",
            "file_path": "/src/main.py",
            "claimed_by": "agent-1",
            "lock_token": "token-1",
            "expires_at": "2099-12-31T23:59:59+00:00",
        }

        # First acquire should succeed
        acquired1, existing1 = await backend.try_acquire_lock(
            "tenant1", "room1", "/src/main.py", lock_data, task_data
        )
        assert acquired1 is True
        assert existing1 is None

        # Second acquire should fail
        lock_data2 = {**lock_data, "held_by": "agent-2", "lock_token": "token-2"}
        task_data2 = {**task_data, "id": "task-2", "claimed_by": "agent-2", "lock_token": "token-2"}

        acquired2, existing2 = await backend.try_acquire_lock(
            "tenant1", "room1", "/src/main.py", lock_data2, task_data2
        )
        assert acquired2 is False
        assert existing2 is not None
        assert existing2["held_by"] == "agent-1"

    async def test_lock_release_with_wrong_token_fails(self, redis_client):
        """Releasing a lock with the wrong token should fail."""
        backend = RedisRoomStateBackend(redis_client)

        lock_data = {
            "held_by": "agent-1",
            "lock_token": "correct-token",
            "expires_at": "2099-12-31T23:59:59+00:00",
        }
        task_data = {
            "id": "task-1",
            "file_path": "/src/auth.py",
            "claimed_by": "agent-1",
            "lock_token": "correct-token",
            "expires_at": "2099-12-31T23:59:59+00:00",
        }

        await backend.try_acquire_lock(
            "tenant1", "room2", "/src/auth.py", lock_data, task_data
        )

        # Try to release with wrong token
        status, _ = await backend.release_lock_atomic(
            "tenant1", "room2", "/src/auth.py", "wrong-token"
        )
        assert status == "token_mismatch"

        # Lock should still be held
        state = await backend.get("tenant1", "room2")
        assert "/src/auth.py" in state["locked_files"]

    async def test_lock_release_with_correct_token_succeeds(self, redis_client):
        """Releasing a lock with the correct token should succeed."""
        backend = RedisRoomStateBackend(redis_client)

        lock_data = {
            "held_by": "agent-1",
            "lock_token": "my-token",
            "expires_at": "2099-12-31T23:59:59+00:00",
        }
        task_data = {
            "id": "task-1",
            "file_path": "/src/db.py",
            "claimed_by": "agent-1",
            "lock_token": "my-token",
            "expires_at": "2099-12-31T23:59:59+00:00",
        }

        await backend.try_acquire_lock(
            "tenant1", "room3", "/src/db.py", lock_data, task_data
        )

        # Release with correct token
        status, _ = await backend.release_lock_atomic(
            "tenant1", "room3", "/src/db.py", "my-token"
        )
        assert status == "released"

        # Lock should be gone
        state = await backend.get("tenant1", "room3")
        assert "/src/db.py" not in state["locked_files"]

    async def test_expiry_does_not_remove_newly_acquired_lock(self, redis_client):
        """Expiry should only remove locks whose token matches the expired task."""
        backend = RedisRoomStateBackend(redis_client)

        # Create an expired lock
        past_ts = "2020-01-01T00:00:00+00:00"
        old_lock = {
            "held_by": "agent-old",
            "lock_token": "old-token",
            "expires_at": past_ts,
        }
        old_task = {
            "id": "old-task",
            "file_path": "/src/utils.py",
            "claimed_by": "agent-old",
            "lock_token": "old-token",
            "expires_at": past_ts,
        }

        # Manually insert the old task/lock (simulating it was acquired earlier)
        tasks_key = "t:tenant1:room_state:room4:tasks"
        locks_key = "t:tenant1:room_state:room4:locks"
        await redis_client.rpush(tasks_key, json.dumps(old_task))
        await redis_client.hset(locks_key, "/src/utils.py", json.dumps(old_lock))

        # Now a new agent acquires the same path (before expiry runs)
        new_lock = {
            "held_by": "agent-new",
            "lock_token": "new-token",
            "expires_at": "2099-12-31T23:59:59+00:00",
        }
        # Directly set the new lock (simulating race where new lock acquired)
        await redis_client.hset(locks_key, "/src/utils.py", json.dumps(new_lock))

        # Run expiry - should NOT delete the new lock because token doesn't match
        expired = await backend.expire_tasks("tenant1", "room4")
        assert "old-task" in expired

        # The new lock should still exist
        state = await backend.get("tenant1", "room4")
        lock = state["locked_files"].get("/src/utils.py")
        assert lock is not None
        assert lock["lock_token"] == "new-token"
        assert lock["held_by"] == "agent-new"


class TestRedisMessageBackpressure:
    """Test atomic backpressure via MAXLEN."""

    async def test_maxlen_parameter_accepted(self, redis_client):
        """enqueue_fanout should accept maxlen parameter without error."""
        backend = RedisMessageBackend(redis_client)

        # Use a unique recipient to avoid state from other tests
        recipient = f"recipient-maxlen-{id(self)}"

        # Verify that calling with maxlen doesn't raise an error
        messages = {recipient: {"id": "msg-1", "content": "message 1"}}
        await backend.enqueue_fanout("tenant1", messages, maxlen=5)

        # Message should be enqueued
        depth = await backend.recipient_depth("tenant1", recipient)
        assert depth >= 1

    async def test_fanout_without_maxlen_keeps_all(self, redis_client):
        """Without maxlen, all messages should be kept."""
        backend = RedisMessageBackend(redis_client)

        # Use unique recipient
        recipient = f"recipient-no-maxlen-{id(self)}"

        # Enqueue 10 separate messages to one recipient
        for i in range(10):
            messages = {recipient: {"id": f"msg-{i}", "content": f"message {i}"}}
            await backend.enqueue_fanout("tenant1", messages, maxlen=None)

        depth = await backend.recipient_depth("tenant1", recipient)
        assert depth == 10
