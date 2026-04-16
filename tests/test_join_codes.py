"""Tests for /v1/join/mint, /v1/join/resolve, /v1/join/install.

Covers the in-memory storage path (no DATABASE_URL required) so this
file runs against the same relay app fixture as the rest of the suite.
Postgres behavior is covered by the service's dual-mode design — if
the in-memory path works, the Postgres path is a thin SQLAlchemy wrap
that we'd regress via integration tests elsewhere.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app
from quorus.services.join_code_svc import normalize_code

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


async def _create_room(client: AsyncClient, name: str = "general") -> None:
    resp = await client.post(
        "/rooms",
        headers=ADMIN_HEADERS,
        json={"name": name, "created_by": "admin"},
    )
    assert resp.status_code in (200, 201), resp.text


# ---------------------------------------------------------------------------
# normalize_code — pure function, no HTTP
# ---------------------------------------------------------------------------


class TestNormalizeCode:
    def test_accepts_canonical(self):
        assert normalize_code("HX4KM7ZP") == "HX4KM7ZP"

    def test_accepts_display_hyphen(self):
        assert normalize_code("HX4K-M7ZP") == "HX4KM7ZP"

    def test_uppercases(self):
        assert normalize_code("hx4k-m7zp") == "HX4KM7ZP"

    def test_strips_surrounding_quotes(self):
        # Straight + smart single + smart double.
        for wrap in ("'", "\"", "\u201c", "\u201d", "\u2018", "\u2019"):
            assert normalize_code(f"{wrap}HX4K-M7ZP{wrap}") == "HX4KM7ZP"

    def test_strips_whitespace(self):
        assert normalize_code("  HX4K-M7ZP  ") == "HX4KM7ZP"

    def test_rejects_non_crockford(self):
        # `O` and `0` should never appear in valid codes.
        assert normalize_code("HX4K-M0ZP") is None
        assert normalize_code("HX4K-MOZP") is None
        assert normalize_code("HX4K-MIZP") is None

    def test_rejects_wrong_length(self):
        assert normalize_code("HX4K") is None
        assert normalize_code("HX4K-M7ZP-EXTRA") is None

    def test_rejects_none(self):
        assert normalize_code(None) is None


# ---------------------------------------------------------------------------
# /v1/join/mint
# ---------------------------------------------------------------------------


class TestMint:
    async def test_requires_auth(self, client: AsyncClient):
        resp = await client.post("/v1/join/mint", json={"room": "general"})
        assert resp.status_code == 401

    async def test_user_role_rejected(self, client: AsyncClient):
        from quorus.auth.tokens import create_jwt

        token = create_jwt(
            sub="alice", tenant_id="t", tenant_slug="ws", role="user",
        )
        await _create_room(client)
        resp = await client.post(
            "/v1/join/mint",
            headers={"Authorization": f"Bearer {token}"},
            json={"room": "general"},
        )
        assert resp.status_code == 403

    async def test_returns_display_code(self, client: AsyncClient):
        await _create_room(client)
        resp = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # `ABCD-EFGH` shape — 4 + hyphen + 4.
        assert len(data["code"]) == 9
        assert data["code"][4] == "-"
        # Canonical chars are Crockford-valid.
        assert normalize_code(data["code"]) is not None
        # URLs point at the test host.
        assert "/v1/join/resolve/" in data["join_url"]
        assert data["install_url"].endswith(".sh")

    async def test_rejects_unknown_room(self, client: AsyncClient):
        resp = await client.post(
            "/v1/join/mint",
            headers=ADMIN_HEADERS,
            json={"room": "does-not-exist"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /v1/join/resolve
# ---------------------------------------------------------------------------


class TestResolve:
    async def test_happy_path(self, client: AsyncClient):
        await _create_room(client)
        mint = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        code = mint.json()["code"]

        # Display form works.
        resp = await client.get(f"/v1/join/resolve/{code}")
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert payload["n"] == "general"
        assert payload["r"].startswith("http")

    async def test_accepts_canonical_form(self, client: AsyncClient):
        await _create_room(client)
        mint = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        canonical = mint.json()["code"].replace("-", "")
        resp = await client.get(f"/v1/join/resolve/{canonical}")
        assert resp.status_code == 200

    async def test_rejects_bad_format(self, client: AsyncClient):
        resp = await client.get("/v1/join/resolve/abc")
        assert resp.status_code == 400

    async def test_unknown_code_is_404(self, client: AsyncClient):
        # Valid Crockford, just unminted.
        resp = await client.get("/v1/join/resolve/AAAA-BBBB")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /v1/join/install/{code}.sh
# ---------------------------------------------------------------------------


class TestInstallScript:
    async def test_returns_bash_with_code_embedded(self, client: AsyncClient):
        await _create_room(client)
        mint = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        code = mint.json()["code"]

        resp = await client.get(f"/v1/join/install/{code}.sh")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-sh")
        body = resp.text
        # Must be a real script.
        assert body.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in body
        # Code must be embedded so `curl | sh` is self-sufficient.
        assert code in body
        # Must invoke the join command.
        assert f'quorus join "{code}"' in body

    async def test_404_on_unknown_code(self, client: AsyncClient):
        resp = await client.get("/v1/join/install/AAAA-BBBB.sh")
        assert resp.status_code == 404

    async def test_400_on_bad_format(self, client: AsyncClient):
        resp = await client.get("/v1/join/install/nope.sh")
        assert resp.status_code == 400
