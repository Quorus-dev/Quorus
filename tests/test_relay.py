import pytest
from httpx import ASGITransport, AsyncClient

from relay_server import app


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
