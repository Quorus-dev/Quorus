"""Tools that need a participant identity work on the relay secret.

0.5.1 agent-path check: on the setup llms.txt teaches (the relay secret),
social_verb, publish/lookup/search_capabilities and list_room_tools answered
``403 legacy auth not permitted on this endpoint`` with no hint. quorus-mcp now
claims the agent's participant key once and retries; when it can't, the 403
names the fix.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient
from quorus_mcp import server

from quorus import bundle_client
from quorus.auth.middleware import LEGACY_AUTH_REFUSED

pytestmark = pytest.mark.asyncio

RELAY = "http://relay.test"
_URL = f"{RELAY}/v1/capabilities"


def _resp(status: int, detail: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail},
                          request=httpx.Request("GET", _URL))


@pytest.fixture
def on_secret():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server, "API_KEY", "")
        mp.setattr(server, "RELAY_SECRET", "s3cret")
        mp.setattr(server, "RELAY_URL", RELAY)
        mp.setattr(server, "INSTANCE_NAME", "alice")
        yield mp


async def test_refused_secret_claims_a_participant_key_and_retries(on_secret):
    seen: dict = {}

    def bearer(relay_url, *, secret, name):
        seen.update(relay_url=relay_url, secret=secret, name=name)
        return "jwt-alice"

    on_secret.setattr(bundle_client, "bearer", bearer)
    sent: list[dict] = []

    async def send(headers):
        sent.append(headers)
        return _resp(200, "ok")

    out = await server._retry_as_participant(_resp(403, LEGACY_AUTH_REFUSED), send)
    assert out.status_code == 200
    assert seen == {"relay_url": RELAY, "secret": "s3cret", "name": "alice"}
    assert sent == [{"Authorization": "Bearer jwt-alice"}]


async def test_failed_claim_returns_a_403_that_names_the_fix(on_secret):
    def bearer(*_a, **_kw):
        raise bundle_client.BundleClientError(
            "the name 'alice' is already claimed on this relay from another machine")

    on_secret.setattr(bundle_client, "bearer", bearer)

    async def send(_headers):  # pragma: no cover - must not resend
        raise AssertionError("resent without a token")

    out = await server._retry_as_participant(_resp(403, LEGACY_AUTH_REFUSED), send)
    assert out.status_code == 403
    message = server._relay_error_message(
        httpx.HTTPStatusError("x", request=out.request, response=out))
    assert "needs a participant key" in message and "already claimed" in message


@pytest.mark.parametrize("api_key, resp", [
    ("mct_key", _resp(403, LEGACY_AUTH_REFUSED)),  # a key is already an identity
    ("", _resp(403, "not a member of this room")),  # a different refusal
    ("", _resp(500, "boom")),
])
async def test_other_responses_pass_through(on_secret, api_key, resp):
    on_secret.setattr(server, "API_KEY", api_key)
    on_secret.setattr(bundle_client, "bearer", lambda *a, **kw: pytest.fail("claimed"))

    async def send(_headers):  # pragma: no cover
        raise AssertionError("resent")

    assert await server._retry_as_participant(resp, send) is resp


async def test_relay_403_names_the_fix():
    from quorus.relay import _reset_state, app

    _reset_state()
    async with AsyncClient(transport=httpx.ASGITransport(app=app),
                           base_url="http://test") as client:
        resp = await client.get("/v1/capabilities/search?q=x",
                                headers={"Authorization": "Bearer test-secret"})
    _reset_state()
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith("legacy auth not permitted")  # quorus-mcp matches this
    assert "QUORUS_API_KEY" in detail
