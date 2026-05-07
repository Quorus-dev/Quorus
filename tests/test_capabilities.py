"""Tests for the capability discovery primitive — Plan v8 Phase 1 A.

Service unit tests + route integration tests covering happy path,
authorization edges, and the search query semantics.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app
from quorus.services.capability_svc import CapabilityError, CapabilitySvc

_TEST_TENANT_ID = "t-cap"
_TEST_TENANT_SLUG = "cap-test"


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


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


class TestCapabilitySvc:
    async def test_publish_then_get_round_trip(self):
        svc = CapabilitySvc()
        out = await svc.publish(
            "tid", "agent-a",
            {"description": "writes Python", "capabilities": ["python"]},
        )
        assert out["participant"] == "agent-a"
        fetched = await svc.get("tid", "agent-a")
        assert fetched is not None
        assert fetched["capabilities"] == ["python"]

    async def test_get_missing_returns_none(self):
        svc = CapabilitySvc()
        assert await svc.get("tid", "ghost") is None

    async def test_search_by_capability_matches(self):
        svc = CapabilitySvc()
        await svc.publish(
            "tid", "py-agent",
            {"capabilities": ["python", "pytest"]},
        )
        await svc.publish(
            "tid", "ts-agent",
            {"capabilities": ["typescript", "next"]},
        )
        results = await svc.search("tid", ["python"])
        assert len(results) == 1
        assert results[0]["participant"] == "py-agent"

    async def test_search_requires_all_tokens(self):
        svc = CapabilitySvc()
        await svc.publish(
            "tid", "a", {"capabilities": ["python", "pytest"]},
        )
        await svc.publish(
            "tid", "b", {"capabilities": ["python"]},
        )
        # Both tokens must match.
        out = await svc.search("tid", ["python", "pytest"])
        names = sorted(m["participant"] for m in out)
        assert names == ["a"]

    async def test_search_substring_case_insensitive(self):
        svc = CapabilitySvc()
        await svc.publish(
            "tid", "a", {"capabilities": ["FastAPI", "Postgres"]},
        )
        out = await svc.search("tid", ["fast"])
        assert len(out) == 1

    async def test_publish_empty_participant_raises(self):
        svc = CapabilitySvc()
        with pytest.raises(CapabilityError):
            await svc.publish("tid", "", {"capabilities": []})

    async def test_search_isolates_by_tenant(self):
        svc = CapabilitySvc()
        await svc.publish(
            "tid-1", "a", {"capabilities": ["python"]},
        )
        await svc.publish(
            "tid-2", "b", {"capabilities": ["python"]},
        )
        out = await svc.search("tid-1", ["python"])
        assert [m["participant"] for m in out] == ["a"]


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


class TestCapabilityRoutes:
    async def test_publish_then_get(self, client: AsyncClient):
        resp = await client.post(
            "/v1/capabilities/alice",
            json={
                "description": "writes Python",
                "capabilities": ["python", "pytest"],
            },
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["manifest"]["participant"] == "alice"

        get_resp = await client.get(
            "/v1/capabilities/alice", headers=_user_headers("alice"),
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["manifest"]["capabilities"] == [
            "python", "pytest",
        ]

    async def test_search_returns_matching_agent(self, client: AsyncClient):
        await client.post(
            "/v1/capabilities/alice",
            json={"capabilities": ["python", "pytest"]},
            headers=_user_headers("alice"),
        )
        await client.post(
            "/v1/capabilities/bob",
            json={"capabilities": ["typescript"]},
            headers=_user_headers("bob"),
        )
        resp = await client.get(
            "/v1/capabilities/search?has=python+pytest",
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["manifests"][0]["participant"] == "alice"

    async def test_cannot_publish_for_another_participant(
        self, client: AsyncClient,
    ):
        # Bob tries to publish for Alice — must 403.
        resp = await client.post(
            "/v1/capabilities/alice",
            json={"capabilities": ["python"]},
            headers=_user_headers("bob"),
        )
        assert resp.status_code == 403

    async def test_admin_can_publish_for_any(self, client: AsyncClient):
        resp = await client.post(
            "/v1/capabilities/alice",
            json={"capabilities": ["python"]},
            headers=_user_headers("admin-bot", role="admin"),
        )
        assert resp.status_code == 200

    async def test_get_unknown_participant_404(
        self, client: AsyncClient,
    ):
        resp = await client.get(
            "/v1/capabilities/ghost", headers=_user_headers("alice"),
        )
        assert resp.status_code == 404

    async def test_legacy_auth_rejected(self, client: AsyncClient):
        # H6 — legacy bearer (test-secret) cannot publish manifests.
        resp = await client.post(
            "/v1/capabilities/alice",
            json={"capabilities": ["python"]},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 403

    async def test_search_no_query_returns_all(self, client: AsyncClient):
        await client.post(
            "/v1/capabilities/alice",
            json={"capabilities": ["python"]},
            headers=_user_headers("alice"),
        )
        resp = await client.get(
            "/v1/capabilities/search", headers=_user_headers("alice"),
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
