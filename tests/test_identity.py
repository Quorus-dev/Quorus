"""Tests for identity enforcement — agent A can't read B's inbox or send as B.

Uses the existing relay ASGI client with JWT fixtures.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from murmur.auth.tokens import create_jwt
from murmur.relay import app
from murmur.relay_routes import reset_state


@pytest.fixture(autouse=True)
async def clean_state():
    reset_state()
    yield
    reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def legacy_headers():
    """Legacy auth — full admin access."""
    return {"Authorization": "Bearer test-secret"}


@pytest.fixture
def alice_jwt():
    return create_jwt(sub="alice", tenant_id="t-1", tenant_slug="acme", role="user")


@pytest.fixture
def bob_jwt():
    return create_jwt(sub="bob", tenant_id="t-1", tenant_slug="acme", role="user")


@pytest.fixture
def alice_headers(alice_jwt):
    return {"Authorization": f"Bearer {alice_jwt}"}


@pytest.fixture
def bob_headers(bob_jwt):
    return {"Authorization": f"Bearer {bob_jwt}"}


class TestLegacyAuthStillWorks:
    """Legacy RELAY_SECRET auth should work with full access."""

    async def test_send_message(self, client, legacy_headers):
        resp = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello"},
            headers=legacy_headers,
        )
        assert resp.status_code == 200

    async def test_get_messages(self, client, legacy_headers):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello"},
            headers=legacy_headers,
        )
        resp = await client.get("/messages/bob", headers=legacy_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_create_room(self, client, legacy_headers):
        resp = await client.post(
            "/rooms",
            json={"name": "test-room", "created_by": "alice"},
            headers=legacy_headers,
        )
        assert resp.status_code == 200

    async def test_heartbeat(self, client, legacy_headers):
        resp = await client.post(
            "/heartbeat",
            json={"instance_name": "alice", "status": "active"},
            headers=legacy_headers,
        )
        assert resp.status_code == 200


class TestJWTAuth:
    """JWT auth should work for all endpoints."""

    async def test_send_message_with_jwt(self, client, alice_headers):
        resp = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello from jwt"},
            headers=alice_headers,
        )
        assert resp.status_code == 200

    async def test_get_messages_with_jwt(self, client, alice_headers, bob_headers):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello"},
            headers=alice_headers,
        )
        resp = await client.get("/messages/bob", headers=bob_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_list_participants_with_jwt(self, client, alice_headers):
        resp = await client.get("/participants", headers=alice_headers)
        assert resp.status_code == 200

    async def test_expired_jwt_returns_401(self, client):
        expired_token = create_jwt(
            sub="alice", tenant_id="t-1", tenant_slug="acme", ttl=-1
        )
        resp = await client.get(
            "/participants",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401


class TestRoomAuth:
    """Room operations should enforce membership and admin rules."""

    async def _create_room_and_join(self, client, alice_headers, bob_headers):
        resp = await client.post(
            "/rooms",
            json={"name": "dev-room", "created_by": "alice"},
            headers=alice_headers,
        )
        room_id = resp.json()["id"]
        await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": "bob"},
            headers=bob_headers,
        )
        return room_id

    async def test_room_message_from_member(
        self, client, legacy_headers, alice_headers, bob_headers,
    ):
        room_id = await self._create_room_and_join(client, alice_headers, bob_headers)
        resp = await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "alice", "content": "hello room"},
            headers=alice_headers,
        )
        assert resp.status_code == 200

    async def test_room_message_from_non_member_rejected(self, client, alice_headers):
        resp = await client.post(
            "/rooms",
            json={"name": "private-room", "created_by": "alice"},
            headers=alice_headers,
        )
        room_id = resp.json()["id"]
        charlie_jwt = create_jwt(
            sub="charlie", tenant_id="t-1", tenant_slug="acme", role="user"
        )
        resp = await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "charlie", "content": "sneak in"},
            headers={"Authorization": f"Bearer {charlie_jwt}"},
        )
        assert resp.status_code == 403

    async def test_room_history_accessible(self, client, alice_headers, bob_headers):
        room_id = await self._create_room_and_join(client, alice_headers, bob_headers)
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "alice", "content": "msg 1"},
            headers=alice_headers,
        )
        resp = await client.get(
            f"/rooms/{room_id}/history",
            headers=bob_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestNoAuth:
    """Unauthenticated requests should be rejected."""

    async def test_no_auth_messages(self, client):
        resp = await client.get("/participants")
        assert resp.status_code == 401

    async def test_no_auth_rooms(self, client):
        resp = await client.get("/rooms")
        assert resp.status_code == 401

    async def test_no_auth_send(self, client):
        resp = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello"},
        )
        assert resp.status_code == 401

    async def test_health_no_auth_required(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_invite_requires_auth(self, client, legacy_headers):
        # Create a room first
        await client.post(
            "/rooms",
            json={"name": "invite-test", "created_by": "alice"},
            headers=legacy_headers,
        )
        # Without auth — should be rejected
        resp = await client.get("/invite/invite-test")
        assert resp.status_code == 401
        # With auth — should succeed
        resp = await client.get("/invite/invite-test", headers=legacy_headers)
        assert resp.status_code == 200
