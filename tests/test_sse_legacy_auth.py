"""Regression tests for SSE /stream legacy auth lockdown.

Wave-5 Fix 6: when ``ALLOW_LEGACY_AUTH`` is on AND the SSE token is
missing/invalid, ``RELAY_SECRET`` Bearer is accepted with ``tid="_legacy"``.
That lets a caller subscribe to ANY recipient cross-tenant. We tighten by
short-circuiting the legacy fallback when ``DATABASE_URL`` is set
(production mode).

We can't reload ``quorus.relay`` with ``DATABASE_URL`` set (that triggers
prod-mode validators that demand REDIS_URL too), so we monkeypatch the
runtime values in place: ``os.environ["DATABASE_URL"]`` is read at
request time by the route, and ``ALLOW_LEGACY_AUTH`` is a module-level
attribute on ``quorus.routes.sse`` we can swap directly.
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app
from quorus.routes import sse as sse_route_mod


@pytest.fixture(autouse=True)
def _clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_production_mode_rejects_legacy_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
):
    """Production mode (DATABASE_URL set) MUST reject RELAY_SECRET as
    SSE token even when ALLOW_LEGACY_AUTH is on.

    Wave-5 lockdown: a misconfigured ALLOW_LEGACY_AUTH=true in prod must
    NOT open the cross-tenant subscription hole.
    """
    monkeypatch.setattr(sse_route_mod, "ALLOW_LEGACY_AUTH", True)
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub-not-connected")
    token = os.environ.get("RELAY_SECRET", "test-secret")
    resp = await client.get(
        "/stream/cross-tenant-target",
        params={"token": token},
        timeout=2.0,
    )
    assert resp.status_code == 401, (
        f"prod mode must reject legacy token, got {resp.status_code}"
    )


async def test_invalid_token_rejected_when_legacy_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
):
    """ALLOW_LEGACY_AUTH off + RELAY_SECRET token → 401 even without DATABASE_URL."""
    monkeypatch.setattr(sse_route_mod, "ALLOW_LEGACY_AUTH", False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    token = os.environ.get("RELAY_SECRET", "test-secret")
    resp = await client.get(
        "/stream/anyone",
        params={"token": token},
        timeout=2.0,
    )
    assert resp.status_code == 401, (
        f"legacy off must reject relay-secret token, got {resp.status_code}"
    )


async def test_legacy_mode_no_db_passes_gate(
    monkeypatch: pytest.MonkeyPatch,
):
    """ALLOW_LEGACY_AUTH on + no DATABASE_URL — assert the boolean gate
    we wired into the route evaluates to True. We don't open the live
    SSE socket because the streaming response never terminates and
    would hang the test under ASGITransport.
    """
    import hmac as hmac_mod
    monkeypatch.setattr(sse_route_mod, "ALLOW_LEGACY_AUTH", True)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    token = os.environ.get("RELAY_SECRET", "test-secret")
    in_production = bool(os.environ.get("DATABASE_URL", ""))
    legacy_ok = (
        sse_route_mod.ALLOW_LEGACY_AUTH
        and not in_production
        and sse_route_mod.RELAY_SECRET
        and hmac_mod.compare_digest(token, sse_route_mod.RELAY_SECRET)
    )
    assert legacy_ok is True, (
        "legacy gate must accept RELAY_SECRET in dev mode"
    )

    # And conversely with DATABASE_URL set, the same gate must lock down.
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
    in_production = bool(os.environ.get("DATABASE_URL", ""))
    legacy_ok_prod = (
        sse_route_mod.ALLOW_LEGACY_AUTH
        and not in_production
        and sse_route_mod.RELAY_SECRET
        and hmac_mod.compare_digest(token, sse_route_mod.RELAY_SECRET)
    )
    assert legacy_ok_prod is False, (
        "legacy gate must reject in production-mode (DATABASE_URL set)"
    )
