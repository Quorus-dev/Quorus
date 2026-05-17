"""Regression tests for the JWT-in-URL audit finding (2026-05-16).

Two leak vectors are closed:

1. Initial dashboard load — the operator-supplied JWT now arrives via URL
   fragment (``#token=...``), not query string (``?token=...``). Fragments are
   never sent to the server, so Fly access logs / CDN logs / Referer headers
   cannot capture the JWT. The legacy ``?token=`` is still accepted with a
   deprecation warning for one release.

2. SSE auth — the short-lived SSE token is now delivered via an HttpOnly,
   Secure, SameSite=Strict cookie scoped to ``/stream``. EventSource forwards
   the cookie automatically when ``withCredentials: true``. Previously the
   token rode in ``?token=`` on the EventSource URL, exposing a 5-minute
   replay window in Fly access logs.
"""

from __future__ import annotations

import os
import re
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


def test_dashboard_reads_token_from_hash_not_query() -> None:
    """The dashboard JS must read its credentials from ``location.hash``.

    Reading from ``location.search`` was the audit finding: query strings hit
    every layer of the network — proxies log them, CDNs cache them, browser
    history persists them, Referer headers leak them cross-origin.
    """
    from quorus.dashboard import DASHBOARD_HTML

    # Hash-based read must be the primary path.
    assert "location.hash" in DASHBOARD_HTML, (
        "dashboard regressed — must read token from URL fragment, not query string"
    )

    # The deprecation warning for ?token= must remain — operators should know
    # their legacy share URLs are leaking.
    assert "deprecated and leaks via server logs" in DASHBOARD_HTML


def test_dashboard_sse_uses_credentials_not_query_token() -> None:
    """EventSource must use ``withCredentials:true`` and must NOT pass ``?token=``.

    Previously: ``new EventSource(url+'?token='+sseToken)``
    Now:        ``new EventSource(url, {withCredentials:true})``
    """
    from quorus.dashboard import DASHBOARD_HTML

    # No query-token construction on the SSE URL.
    bad = re.compile(r"new\s+EventSource\([^)]*\?token=")
    assert bad.search(DASHBOARD_HTML) is None, (
        "dashboard regressed — EventSource URL contains ?token= "
        "(SSE auth must come from HttpOnly cookie via /stream/token)"
    )

    # Withcredentials is the new path.
    assert "withCredentials:true" in DASHBOARD_HTML, (
        "EventSource must forward credentials so the HttpOnly SSE cookie is sent"
    )


async def test_stream_token_endpoint_sets_httponly_cookie(client):
    """POST /stream/token must Set-Cookie: quorus_sse with HttpOnly, Secure,
    SameSite=Strict, Path=/stream."""
    resp = await client.post(
        "/stream/token", json={"recipient": "admin"}, headers=AUTH
    )
    # The handler requires JWT.sub == recipient under non-legacy auth.
    # In tests we use legacy RELAY_SECRET (admin role), recipient is free.
    assert resp.status_code == 200, resp.text

    # httpx exposes Set-Cookie headers via .headers but the parsed cookie jar
    # via .cookies. We need the raw Set-Cookie header to assert the security
    # flags (HttpOnly, Secure, SameSite=Strict).
    raw_set_cookie = ""
    for k, v in resp.headers.items():
        if k.lower() == "set-cookie" and v.startswith("quorus_sse="):
            raw_set_cookie = v
            break
    assert raw_set_cookie, "Set-Cookie: quorus_sse=... not present on response"
    lowered = raw_set_cookie.lower()
    assert "httponly" in lowered, "SSE cookie missing HttpOnly"
    assert "secure" in lowered, "SSE cookie missing Secure"
    assert "samesite=strict" in lowered, "SSE cookie missing SameSite=Strict"
    assert "path=/stream" in lowered, "SSE cookie not scoped to /stream"


def test_stream_get_handler_reads_cookie_when_query_empty():
    """Source-level check: the SSE handler must read from cookies when the
    query-string token is empty. Functional verification of the full SSE
    handshake would require streaming the response, which deadlocks under
    httpx + ASGI without a real network buffer; we trust the unit-level
    source assertion + the cookie-set test above."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "quorus" / "routes" / "sse.py").read_text()
    assert "if not token:" in src, "SSE handler missing query-empty branch"
    assert "request.cookies.get(_SSE_COOKIE_NAME" in src, (
        "SSE handler must fall back to the quorus_sse cookie when ?token= is absent"
    )


async def test_stream_get_rejects_no_token_no_cookie(client):
    """Absent both query token and cookie, /stream/* must 401."""
    resp = await client.get("/stream/admin", timeout=2.0)
    assert resp.status_code == 401
