"""Tests for the tool catalog primitive — Plan v8 Phase 1 B.

Service unit tests + route integration covering registration, listing,
removal, room-membership gating, and admin-only writes.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app
from quorus.services.tool_catalog_svc import (
    DuplicateToolError,
    ToolCatalogError,
    ToolCatalogSvc,
)

_TEST_TENANT_ID = "t-tools"
_TEST_TENANT_SLUG = "tools-test"


def _user_headers(name: str, role: str = "user") -> dict[str, str]:
    token = create_jwt(
        sub=name,
        tenant_id=_TEST_TENANT_ID,
        tenant_slug=_TEST_TENANT_SLUG,
        role=role,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def _clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def room_id(client: AsyncClient) -> str:
    resp = await client.post(
        "/rooms",
        json={"name": "tools-room", "created_by": "alice"},
        headers=_user_headers("alice"),
    )
    assert resp.status_code == 200, resp.text
    rid = resp.json()["id"]
    join = await client.post(
        f"/rooms/{rid}/join",
        json={"participant": "bob", "role": "member"},
        headers=_user_headers("alice"),
    )
    assert join.status_code == 200
    return rid


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


class TestToolCatalogSvc:
    async def test_register_then_list(self):
        svc = ToolCatalogSvc()
        out = await svc.register(
            "tid", "rid",
            name="github-mcp", url="https://example.com",
            registered_by="alice",
        )
        assert out["name"] == "github-mcp"
        items = await svc.list("tid", "rid")
        assert len(items) == 1

    async def test_duplicate_registration_rejected(self):
        svc = ToolCatalogSvc()
        await svc.register(
            "tid", "rid",
            name="dup", url="https://x.com", registered_by="alice",
        )
        with pytest.raises(DuplicateToolError):
            await svc.register(
                "tid", "rid",
                name="dup", url="https://y.com", registered_by="alice",
            )

    async def test_remove_then_get_none(self):
        svc = ToolCatalogSvc()
        await svc.register(
            "tid", "rid",
            name="t", url="https://x.com", registered_by="alice",
        )
        assert await svc.remove("tid", "rid", "t") is True
        assert await svc.get("tid", "rid", "t") is None

    async def test_remove_unknown_returns_false(self):
        svc = ToolCatalogSvc()
        assert await svc.remove("tid", "rid", "ghost") is False

    async def test_invalid_access_rejected(self):
        svc = ToolCatalogSvc()
        with pytest.raises(ToolCatalogError):
            await svc.register(
                "tid", "rid",
                name="t", url="https://x.com",
                registered_by="alice", access="cosmic",
            )

    async def test_isolation_per_room(self):
        svc = ToolCatalogSvc()
        await svc.register(
            "tid", "room-a",
            name="t", url="https://x.com", registered_by="alice",
        )
        items = await svc.list("tid", "room-b")
        assert items == []


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


class TestToolCatalogRoutes:
    async def test_register_then_list(
        self, client: AsyncClient, room_id: str,
    ):
        resp = await client.post(
            f"/v1/rooms/{room_id}/tools",
            json={
                "name": "github-mcp",
                "url": "https://mcp.example.com",
                "access": "room",
                "description": "GitHub repo + PR ops",
            },
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 200, resp.text
        list_resp = await client.get(
            f"/v1/rooms/{room_id}/tools",
            headers=_user_headers("alice"),
        )
        assert list_resp.status_code == 200
        body = list_resp.json()
        assert body["count"] == 1
        assert body["tools"][0]["name"] == "github-mcp"

    async def test_non_member_cannot_list(
        self, client: AsyncClient, room_id: str,
    ):
        # Carol is in the same tenant but not a room member.
        resp = await client.get(
            f"/v1/rooms/{room_id}/tools",
            headers=_user_headers("carol"),
        )
        assert resp.status_code == 403

    async def test_non_admin_member_cannot_register(
        self, client: AsyncClient, room_id: str,
    ):
        # Bob joined as a regular member; alice is room creator (admin).
        resp = await client.post(
            f"/v1/rooms/{room_id}/tools",
            json={
                "name": "x", "url": "https://x.com", "access": "room",
            },
            headers=_user_headers("bob"),
        )
        assert resp.status_code == 403

    async def test_duplicate_registration_returns_409(
        self, client: AsyncClient, room_id: str,
    ):
        body = {
            "name": "dup", "url": "https://x.com", "access": "room",
        }
        first = await client.post(
            f"/v1/rooms/{room_id}/tools", json=body,
            headers=_user_headers("alice"),
        )
        assert first.status_code == 200
        second = await client.post(
            f"/v1/rooms/{room_id}/tools", json=body,
            headers=_user_headers("alice"),
        )
        assert second.status_code == 409

    async def test_admin_can_remove(
        self, client: AsyncClient, room_id: str,
    ):
        await client.post(
            f"/v1/rooms/{room_id}/tools",
            json={
                "name": "rm-me", "url": "https://x.com", "access": "room",
            },
            headers=_user_headers("alice"),
        )
        resp = await client.delete(
            f"/v1/rooms/{room_id}/tools/rm-me",
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 200

    async def test_remove_unknown_returns_404(
        self, client: AsyncClient, room_id: str,
    ):
        resp = await client.delete(
            f"/v1/rooms/{room_id}/tools/ghost",
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 404

    async def test_invalid_url_rejected(
        self, client: AsyncClient, room_id: str,
    ):
        resp = await client.post(
            f"/v1/rooms/{room_id}/tools",
            json={"name": "t", "url": "not-a-url", "access": "room"},
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 422
