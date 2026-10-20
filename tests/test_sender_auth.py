"""The relay stamps each room message with how its sender signed in (release
backport of 94e13e26's stamping half; loop B's verdict pinning stays on weekend)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.middleware import AuthContext
from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app
from quorus.services import p1_persistence, sender_auth

ADMIN = {"Authorization": "Bearer test-secret"}
A, B = "a" * 40, "b" * 40
T0 = 1_800_000_000.0


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(p1_persistence, "get_redis_or_none", lambda: None)
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/rooms", json={"name": "build", "created_by": "arav"}, headers=ADMIN)
        assert r.status_code == 200, r.text
        yield c
    _reset_state()


async def _key_login(c: AsyncClient, name: str) -> dict:
    r = await c.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    key = (await c.post("/v1/join/redeem", json={"code": payload["c"], "name": name})
           ).json()["participant_key"]
    jwt = (await c.post("/v1/auth/token", json={"api_key": key})).json()["token"]
    return {"Authorization": f"Bearer {jwt}"}


async def _say(c: AsyncClient, name: str, text: str, headers: dict, **body) -> None:
    r = await c.post("/rooms/build/messages",
                     json={"from_name": name, "content": text, **body}, headers=headers)
    assert r.status_code == 200, r.text


async def _history(c: AsyncClient) -> list[dict]:
    r = await c.get("/rooms/build/history", params={"limit": 50}, headers=ADMIN)
    return [{**m, "timestamp": T0 + 60} for m in r.json()]


def _claim(name: str) -> str:
    return app.state.participant_keys.info(name)["claim_id"]


def test_the_stamp_comes_from_the_verified_login() -> None:
    assert sender_auth.stamp(AuthContext(is_legacy=True), "_legacy") == {
        "sender_auth": "secret", "sender_tenant": "_legacy"}
    key = AuthContext(sub="bob", tenant_id="_legacy", claims={"pk_claim": "c1"})
    assert sender_auth.stamp(key, "_legacy") == {
        "sender_auth": "key", "sender_tenant": "_legacy", "sender_claim_id": "c1"}
    hosted = AuthContext(sub="carol", participant_id="p-carol", tenant_id="t1")
    assert sender_auth.stamp(hosted, "t1") == {
        "sender_auth": "jwt", "sender_tenant": "t1", "sender_participant_id": "p-carol"}


async def test_history_carries_the_stamp_and_ignores_a_forged_one(client) -> None:
    bob = await _key_login(client, "bob")
    forged = {"sender_auth": "key", "sender_claim_id": "claim-x", "sender_tenant": "t9"}
    await _say(client, "arav", "secret post", ADMIN, **forged)
    await _say(client, "bob", "key post", bob, **forged)
    by_text = {m["content"]: m for m in await _history(client)}
    assert by_text["secret post"]["sender_auth"] == "secret"
    assert "sender_claim_id" not in by_text["secret post"]
    assert by_text["key post"]["sender_auth"] == "key"
    assert by_text["key post"]["sender_claim_id"] == _claim("bob")
    assert by_text["key post"]["sender_tenant"] == "_legacy"


async def test_a_hosted_login_is_stamped_with_its_participant_id(client) -> None:
    carol = {"Authorization": "Bearer " + create_jwt(
        sub="carol", tenant_id="t-host", tenant_slug="host", participant_id="p-carol")}
    r = await client.post("/rooms", json={"name": "hosted", "created_by": "carol"},
                          headers=carol)
    assert r.status_code == 200, r.text
    r = await client.post("/rooms/hosted/messages",
                          json={"from_name": "carol", "content": "hi"}, headers=carol)
    assert r.status_code == 200, r.text
    [msg] = (await client.get("/rooms/hosted/history", headers=carol)).json()
    assert (msg["sender_auth"], msg["sender_participant_id"], msg["sender_tenant"]) == (
        "jwt", "p-carol", "t-host")
