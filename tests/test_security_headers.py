"""Regression tests for HTTP security headers (2026-05-16 audit).

Every relay response (success or 404) MUST carry:
- ``Strict-Transport-Security``: force HTTPS for 2 years across all subdomains
- ``X-Content-Type-Options: nosniff``: defeat MIME-sniffing attacks
- ``X-Frame-Options: DENY``: defeat clickjacking
- ``Referrer-Policy: no-referrer``: never leak the relay URL to third parties

HTML responses additionally carry a strict CSP. These tests ensure the
middleware doesn't silently regress as new routes / middleware layers are
added.
"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("RELAY_SECRET", "test-secret")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app


AUTH = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
def clean():
    _reset_state()
    yield
    _reset_state()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


REQUIRED_HEADERS = {
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def _assert_security_headers(resp) -> None:
    for header, expected in REQUIRED_HEADERS.items():
        assert header in resp.headers, f"missing security header: {header}"
        assert resp.headers[header] == expected, (
            f"header {header} = {resp.headers[header]!r}, expected {expected!r}"
        )


async def test_security_headers_on_health_endpoint(client):
    resp = await client.get("/health")
    _assert_security_headers(resp)


async def test_security_headers_on_404(client):
    """404 responses must still carry the security headers — attackers shouldn't
    learn anything from the difference between a real 404 and a real response."""
    resp = await client.get("/this-does-not-exist-and-never-will")
    assert resp.status_code == 404
    _assert_security_headers(resp)


async def test_security_headers_on_authed_endpoint(client):
    resp = await client.post(
        "/rooms", json={"name": "header-test", "created_by": "admin"}, headers=AUTH
    )
    _assert_security_headers(resp)


async def test_security_headers_on_401(client):
    """Auth failures must also carry the headers (defense in depth)."""
    resp = await client.post(
        "/rooms",
        json={"name": "header-test", "created_by": "admin"},
        headers={"Authorization": "Bearer bogus"},
    )
    assert resp.status_code in (401, 403)
    _assert_security_headers(resp)


async def test_html_response_has_csp(client):
    """HTML responses (dashboard, invite pages) need a CSP on top of the
    baseline headers."""
    resp = await client.get("/dashboard")
    if resp.status_code == 200 and "text/html" in resp.headers.get(
        "content-type", ""
    ):
        assert "content-security-policy" in resp.headers
        assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
