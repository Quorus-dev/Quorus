import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer test-secret"}


async def test_two_instances_conversation(client: AsyncClient):
    """Simulate alice and bob exchanging messages through the relay."""
    # Alice sends to bob
    resp = await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "Hey bob, how are you?"},
        headers=HEADERS,
    )
    assert resp.status_code == 200

    # Bob checks messages
    resp = await client.get("/messages/bob?ack=server", headers=HEADERS)
    messages = resp.json()
    assert len(messages) == 1
    assert messages[0]["from_name"] == "alice"
    assert messages[0]["content"] == "Hey bob, how are you?"

    # Bob replies to alice
    resp = await client.post(
        "/messages",
        json={"from_name": "bob", "to": "alice", "content": "Good! Working on the feature."},
        headers=HEADERS,
    )
    assert resp.status_code == 200

    # Alice checks messages
    resp = await client.get("/messages/alice?ack=server", headers=HEADERS)
    messages = resp.json()
    assert len(messages) == 1
    assert messages[0]["from_name"] == "bob"
    assert messages[0]["content"] == "Good! Working on the feature."

    # Both are participants
    resp = await client.get("/participants", headers=HEADERS)
    participants = resp.json()
    assert "alice" in participants
    assert "bob" in participants

    # Analytics should reflect the conversation
    resp = await client.get("/analytics", headers=HEADERS)
    data = resp.json()
    assert data["total_messages_sent"] == 2
    assert data["total_messages_delivered"] == 2
    assert "alice" in data["participants"]
    assert "bob" in data["participants"]

    # No more messages for either
    resp = await client.get("/messages/alice?ack=server", headers=HEADERS)
    assert resp.json() == []
    resp = await client.get("/messages/bob?ack=server", headers=HEADERS)
    assert resp.json() == []


async def test_room_broadcast_and_history(client: AsyncClient):
    """Room messages fan-out to members and appear in history."""
    # Create room and join two agents
    await client.post(
        "/rooms",
        json={"name": "collab", "created_by": "agent-a"},
        headers=HEADERS,
    )
    await client.post("/rooms/collab/join", json={"participant": "agent-a"}, headers=HEADERS)
    await client.post("/rooms/collab/join", json={"participant": "agent-b"}, headers=HEADERS)

    # agent-a posts a message
    resp = await client.post(
        "/rooms/collab/messages",
        json={"from_name": "agent-a", "content": "starting work", "message_type": "status"},
        headers=HEADERS,
    )
    assert resp.status_code == 200

    # agent-b should receive it in their inbox
    resp = await client.get("/messages/agent-b?ack=server", headers=HEADERS)
    msgs = resp.json() if isinstance(resp.json(), list) else resp.json().get("messages", [])
    contents = [m["content"] for m in msgs]
    assert "starting work" in contents

    # History should record it
    resp = await client.get("/rooms/collab/history", headers=HEADERS)
    history = resp.json()
    assert any(m["content"] == "starting work" for m in history)


async def test_primitive_b_lock_lifecycle(client: AsyncClient):
    """Full lock/unlock cycle: acquire → state reflects lock → release → cleared."""
    # Create room and join agent
    await client.post(
        "/rooms",
        json={"name": "lockroom", "created_by": "worker"},
        headers=HEADERS,
    )
    await client.post("/rooms/lockroom/join", json={"participant": "worker"}, headers=HEADERS)

    # Acquire lock
    resp = await client.post(
        "/rooms/lockroom/lock",
        json={"file_path": "src/core.py", "claimed_by": "worker", "ttl_seconds": 300},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["locked"] is False
    lock_token = data["lock_token"]

    # State should reflect the lock
    resp = await client.get("/rooms/lockroom/state", headers=HEADERS)
    state = resp.json()
    assert "src/core.py" in state["locked_files"]
    assert state["locked_files"]["src/core.py"]["held_by"] == "worker"

    # Second agent cannot take the same lock
    resp = await client.post(
        "/rooms/lockroom/lock",
        json={"file_path": "src/core.py", "claimed_by": "interloper", "ttl_seconds": 60},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["locked"] is True
    assert resp.json()["held_by"] == "worker"

    # Release the lock
    resp = await client.request(
        "DELETE",
        "/rooms/lockroom/lock/src/core.py",
        json={"lock_token": lock_token},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["released"] is True

    # State should now show no lock
    resp = await client.get("/rooms/lockroom/state", headers=HEADERS)
    state = resp.json()
    assert "src/core.py" not in state["locked_files"]
