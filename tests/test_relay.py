import pytest
from httpx import ASGITransport, AsyncClient

from relay_server import _reset_state, app


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


async def test_health_check(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_no_auth_returns_401(client: AsyncClient):
    resp = await client.get("/participants")
    assert resp.status_code == 401


async def test_wrong_auth_returns_401(client: AsyncClient):
    resp = await client.get("/participants", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


async def test_correct_auth_returns_200(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/participants", headers=auth_headers)
    assert resp.status_code == 200


async def test_send_message(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hello bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert "timestamp" in data


async def test_receive_messages(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "msg1"},
        headers=auth_headers,
    )
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "msg2"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 2
    assert messages[0]["content"] == "msg1"
    assert messages[1]["content"] == "msg2"
    assert messages[0]["from_name"] == "alice"
    assert "id" in messages[0]
    assert "timestamp" in messages[0]


async def test_receive_clears_messages(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hello"},
        headers=auth_headers,
    )
    await client.get("/messages/bob", headers=auth_headers)
    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.json() == []


async def test_receive_no_messages(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/messages/nobody", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_send_registers_participant(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "charlie", "to": "dave", "content": "hi"},
        headers=auth_headers,
    )
    resp = await client.get("/participants", headers=auth_headers)
    participants = resp.json()
    assert "charlie" in participants
