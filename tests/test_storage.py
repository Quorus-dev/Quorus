"""Tests for storage layer — QueueBackend protocol compliance against InMemoryBackend."""

import pytest

from murmur.storage.backend import QueueBackend
from murmur.storage.memory import InMemoryBackend


@pytest.fixture
def backend():
    return InMemoryBackend()


class TestInMemoryBackend:
    @pytest.mark.asyncio
    async def test_protocol_compliance(self, backend):
        assert isinstance(backend, QueueBackend)

    @pytest.mark.asyncio
    async def test_enqueue_and_dequeue(self, backend):
        msg = {"id": "1", "content": "hello", "from_name": "alice"}
        await backend.enqueue("tenant-1", "bob", msg)
        messages = await backend.dequeue_all("tenant-1", "bob")
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_dequeue_clears_queue(self, backend):
        await backend.enqueue("tenant-1", "bob", {"id": "1", "content": "first"})
        await backend.enqueue("tenant-1", "bob", {"id": "2", "content": "second"})

        messages = await backend.dequeue_all("tenant-1", "bob")
        assert len(messages) == 2

        # Second dequeue should be empty
        messages = await backend.dequeue_all("tenant-1", "bob")
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_peek_count(self, backend):
        assert await backend.peek("tenant-1", "bob") == 0

        await backend.enqueue("tenant-1", "bob", {"id": "1", "content": "hello"})
        assert await backend.peek("tenant-1", "bob") == 1

        await backend.enqueue("tenant-1", "bob", {"id": "2", "content": "world"})
        assert await backend.peek("tenant-1", "bob") == 2

    @pytest.mark.asyncio
    async def test_peek_does_not_consume(self, backend):
        await backend.enqueue("tenant-1", "bob", {"id": "1", "content": "hello"})
        await backend.peek("tenant-1", "bob")
        messages = await backend.dequeue_all("tenant-1", "bob")
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, backend):
        await backend.enqueue("tenant-1", "bob", {"id": "1", "content": "t1"})
        await backend.enqueue("tenant-2", "bob", {"id": "2", "content": "t2"})

        t1_msgs = await backend.dequeue_all("tenant-1", "bob")
        t2_msgs = await backend.dequeue_all("tenant-2", "bob")

        assert len(t1_msgs) == 1
        assert t1_msgs[0]["content"] == "t1"
        assert len(t2_msgs) == 1
        assert t2_msgs[0]["content"] == "t2"

    @pytest.mark.asyncio
    async def test_recipient_isolation(self, backend):
        await backend.enqueue("tenant-1", "alice", {"id": "1", "content": "for alice"})
        await backend.enqueue("tenant-1", "bob", {"id": "2", "content": "for bob"})

        alice_msgs = await backend.dequeue_all("tenant-1", "alice")
        bob_msgs = await backend.dequeue_all("tenant-1", "bob")

        assert len(alice_msgs) == 1
        assert alice_msgs[0]["content"] == "for alice"
        assert len(bob_msgs) == 1
        assert bob_msgs[0]["content"] == "for bob"

    @pytest.mark.asyncio
    async def test_enqueue_batch(self, backend):
        messages = [
            {"id": "1", "content": "chunk-0", "chunk_index": 0},
            {"id": "2", "content": "chunk-1", "chunk_index": 1},
            {"id": "3", "content": "chunk-2", "chunk_index": 2},
        ]
        await backend.enqueue_batch("tenant-1", "bob", messages)
        result = await backend.dequeue_all("tenant-1", "bob")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_enqueue_preserves_order(self, backend):
        for i in range(10):
            await backend.enqueue("tenant-1", "bob", {"id": str(i), "content": f"msg-{i}"})

        messages = await backend.dequeue_all("tenant-1", "bob")
        assert [m["content"] for m in messages] == [f"msg-{i}" for i in range(10)]

    @pytest.mark.asyncio
    async def test_clear(self, backend):
        await backend.enqueue("tenant-1", "bob", {"id": "1", "content": "hello"})
        backend.clear()
        assert await backend.peek("tenant-1", "bob") == 0

    @pytest.mark.asyncio
    async def test_empty_dequeue(self, backend):
        messages = await backend.dequeue_all("tenant-1", "nonexistent")
        assert messages == []
