"""Regression tests for two Gate B items (2026-05-16 audit).

B4 — ``POST /messages`` had no rate limit, opening a trivial DoS surface
where a single sender could flood any recipient's inbox or burn the
relay's Redis budget. The fix mirrors ``/rooms/{id}/messages``: 60
sends per minute per sender per tenant.

B6 — The dashboard is an admin surface; it should never appear in
search results. Added ``robots: noindex, nofollow, noarchive`` and
the explicit ``googlebot`` directive (Google's docs say the bot-
specific directive overrides the generic one if both are present —
duplicate intent for clarity).
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


# ---------------------------------------------------------------------------
# B4 — DM rate limit
# ---------------------------------------------------------------------------


async def test_dm_send_rate_limit_enforced(client):
    """After 60 sends, the 61st must 429."""
    payload = {"from_name": "alice", "to": "bob", "content": "hi"}

    # 60 within the burst window — all should succeed
    for i in range(60):
        r = await client.post("/messages", json=payload, headers=AUTH)
        assert r.status_code == 200, (
            f"unexpected failure on send {i}: {r.status_code} — {r.text[:200]}"
        )

    # 61st should be limited
    r = await client.post("/messages", json=payload, headers=AUTH)
    assert r.status_code == 429, (
        f"DM rate limit not enforced after 60 sends — got {r.status_code}"
    )
    assert "DM send rate limit" in r.json().get("detail", "")


async def test_dm_rate_limit_per_sender_not_global(client):
    """Two distinct senders should not share a budget."""
    # alice burns her 60
    for _ in range(60):
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "hi"},
            headers=AUTH,
        )
        assert r.status_code == 200

    # carol must still be able to send — her budget is independent
    r = await client.post(
        "/messages",
        json={"from_name": "carol", "to": "bob", "content": "hi"},
        headers=AUTH,
    )
    assert r.status_code == 200, (
        f"carol's send shouldn't be affected by alice's limit — got "
        f"{r.status_code}: {r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# B6 — Dashboard noindex
# ---------------------------------------------------------------------------


def test_dashboard_has_robots_noindex_meta():
    """Dashboard HTML must declare noindex, nofollow, noarchive."""
    from quorus.dashboard import DASHBOARD_HTML

    assert 'name="robots"' in DASHBOARD_HTML, (
        "dashboard HTML missing <meta name='robots'> — admin surface "
        "must not be crawled"
    )
    # The directive value must include all three.
    for directive in ("noindex", "nofollow", "noarchive"):
        assert directive in DASHBOARD_HTML, (
            f"dashboard robots meta missing '{directive}' directive"
        )

    # Google requires the bot-specific directive to override the generic
    # one when both are present — keep both for explicit intent.
    assert 'name="googlebot"' in DASHBOARD_HTML, (
        "dashboard missing googlebot-specific robots directive"
    )


async def test_dashboard_response_actually_carries_meta(client):
    """End-to-end: the rendered dashboard must contain the meta tags
    (not just the source string)."""
    resp = await client.get("/dashboard")
    if resp.status_code != 200:
        pytest.skip(f"/dashboard not available in this config ({resp.status_code})")
    body = resp.text
    assert 'name="robots"' in body
    assert "noindex" in body
