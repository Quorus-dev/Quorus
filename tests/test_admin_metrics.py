"""Tests for the admin metrics endpoint and HTML dashboard.

Covers:
  - Auth: admin-role JWT + legacy RELAY_SECRET pass; user-role is blocked
  - Limited mode: without DATABASE_URL, metrics degrade gracefully
  - Schema: shape + zero-fill + cached result
  - Dashboard HTML: login roundtrip with cookie, wrong password, logout
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app

ADMIN_HEADERS = {"Authorization": "Bearer test-secret"}


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


# ---------------------------------------------------------------------------
# JSON endpoint — /admin/metrics
# ---------------------------------------------------------------------------


class TestAdminMetricsAuth:
    async def test_rejects_missing_auth(self, client: AsyncClient):
        resp = await client.get("/admin/metrics")
        assert resp.status_code == 401

    async def test_legacy_secret_grants_admin(self, client: AsyncClient):
        resp = await client.get("/admin/metrics", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

    async def test_user_role_jwt_rejected(self, client: AsyncClient):
        # Build a user-role JWT the same way the auth flow does.
        from quorus.auth.tokens import create_jwt

        token = create_jwt(
            sub="alice",
            tenant_id="t-1",
            tenant_slug="acme",
            role="user",
        )
        resp = await client.get(
            "/admin/metrics", headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


class TestAdminMetricsShape:
    async def test_limited_mode_schema(self, client: AsyncClient):
        # Unset DATABASE_URL to force limited mode even if something else
        # pokes it. `os.environ.pop(...)` is idempotent.
        prior = os.environ.pop("DATABASE_URL", None)
        try:
            resp = await client.get("/admin/metrics", headers=ADMIN_HEADERS)
        finally:
            if prior is not None:
                os.environ["DATABASE_URL"] = prior
        assert resp.status_code == 200
        data = resp.json()

        # Top-level shape
        assert data["mode"] == "limited"
        assert "generated_at" in data
        assert data["total_workspaces"] is None
        assert data["new_workspaces"] is None
        assert data["active_users"] is None

        # Messages shape — always present, even in limited mode
        messages = data["messages"]
        assert isinstance(messages["per_day"], list)
        assert len(messages["per_day"]) == 30  # default days window
        for item in messages["per_day"]:
            assert set(item.keys()) == {"date", "count"}
            assert isinstance(item["count"], int)

        assert data["top_workspaces"] == []
        assert "note" in data  # explains why the workspace block is empty

    async def test_days_param_controls_window_length(self, client: AsyncClient):
        resp = await client.get(
            "/admin/metrics?days=7", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert len(resp.json()["messages"]["per_day"]) == 7

    async def test_days_param_clamped(self, client: AsyncClient):
        # FastAPI Query(ge=1, le=90) rejects out-of-range with 422.
        resp = await client.get(
            "/admin/metrics?days=9999", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422

    async def test_response_is_cached_across_calls(self, client: AsyncClient):
        r1 = await client.get("/admin/metrics", headers=ADMIN_HEADERS)
        r2 = await client.get("/admin/metrics", headers=ADMIN_HEADERS)
        assert r1.status_code == r2.status_code == 200
        # `generated_at` is frozen while the 30s cache is warm
        assert r1.json()["generated_at"] == r2.json()["generated_at"]


# ---------------------------------------------------------------------------
# HTML dashboard — /admin/login, /admin/dashboard
# ---------------------------------------------------------------------------


class TestAdminDashboard:
    async def test_login_form_renders(self, client: AsyncClient):
        resp = await client.get("/admin/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        assert "Relay secret" in body
        assert "relay analytics" not in body  # no dashboard leak

    async def test_dashboard_redirects_without_auth(self, client: AsyncClient):
        # follow_redirects=False so we see the 303
        resp = await client.get("/admin/dashboard", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/admin/login")

    async def test_dashboard_accepts_bearer(self, client: AsyncClient):
        resp = await client.get("/admin/dashboard", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert "relay analytics" in resp.text
        # Sparkline SVG is always present, even with zero data
        assert "<svg" in resp.text or "no data yet" in resp.text

    async def test_login_wrong_secret_redirects_with_error(
        self, client: AsyncClient,
    ):
        resp = await client.post(
            "/admin/login",
            data={"secret": "not-the-real-secret"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]
        # No cookie set on failure
        assert "quorus_admin" not in resp.headers.get("set-cookie", "")

    async def test_login_correct_secret_sets_cookie(self, client: AsyncClient):
        resp = await client.post(
            "/admin/login",
            data={"secret": "test-secret"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/dashboard"
        cookie_header = resp.headers.get("set-cookie", "")
        assert "quorus_admin=" in cookie_header
        assert "HttpOnly" in cookie_header

    async def test_logout_clears_cookie(self, client: AsyncClient):
        resp = await client.post("/admin/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"
        cookie_header = resp.headers.get("set-cookie", "")
        # delete_cookie sets Max-Age=0 or expires in the past
        assert "quorus_admin=" in cookie_header
