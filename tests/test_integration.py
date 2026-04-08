import pytest
from httpx import ASGITransport, AsyncClient

from relay_server import _reset_state, app


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
    resp = await client.get("/messages/bob", headers=HEADERS)
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
    resp = await client.get("/messages/alice", headers=HEADERS)
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
    resp = await client.get("/messages/alice", headers=HEADERS)
    assert resp.json() == []
    resp = await client.get("/messages/bob", headers=HEADERS)
    assert resp.json() == []
