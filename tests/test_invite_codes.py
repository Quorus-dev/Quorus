"""Invites without the shared secret (docs/weekend/invite-roles.md, step 1).

An invite on a shared-secret relay used to carry RELAY_SECRET, which is admin.
Now it carries an opaque, use-limited code that redeems into the joiner's own
participant key, spent atomically.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth import invite_codes
from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}


@pytest.fixture
async def client(tmp_path):
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/rooms", json={"name": "build", "created_by": "arav"}, headers=ADMIN)
        assert r.status_code == 200, r.text
        yield c
    _reset_state()


async def _mint(c: AsyncClient, **kw) -> dict:
    r = await c.post("/v1/join/mint", json={"room": "build", **kw}, headers=ADMIN)
    assert r.status_code == 200, r.text
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    return payload


async def test_an_invite_never_carries_the_secret(client: AsyncClient) -> None:
    r = await client.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    code = r.json()["code"]
    resolved = await client.get(f"/v1/join/resolve/{code}")
    script = await client.get(f"/v1/join/install/{code}.sh")
    for text in (r.text, resolved.text, script.text):
        assert "test-secret" not in text
    payload = resolved.json()["payload"]
    assert payload["v"] == 2 and payload["c"].startswith("qi_") and "s" not in payload


async def test_redeem_gives_your_own_key_for_that_room_only(client: AsyncClient) -> None:
    payload = await _mint(client)
    r = await client.post("/v1/join/redeem", json={"code": payload["c"], "name": "bob"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["room"] == "build" and body["role"] == "member"
    assert body["participant_key"].startswith("quorus_pk_")
    jwt = (await client.post("/v1/auth/token", json={"api_key": body["participant_key"]}))
    bob = {"Authorization": f"Bearer {jwt.json()['token']}"}
    assert (await client.get("/rooms/build/history", headers=bob)).status_code == 200
    await client.post("/rooms", json={"name": "private", "created_by": "arav"}, headers=ADMIN)
    assert (await client.get("/rooms/private/history", headers=bob)).status_code == 403
    assert app.state.participant_keys.info("bob")["kind"] == "human"


async def test_single_use_revoked_and_expired_codes_fail(client: AsyncClient) -> None:
    payload = await _mint(client)
    ok = await client.post("/v1/join/redeem", json={"code": payload["c"], "name": "bob"})
    assert ok.status_code == 200
    again = await client.post("/v1/join/redeem", json={"code": payload["c"], "name": "carol"})
    assert again.status_code == 403

    store = app.state.participant_keys
    code, inv = invite_codes.mint(store, room_id="x", room_name="build", role="member",
                                  issuer="arav", ttl_s=3600)
    assert invite_codes.revoke(store, inv.id)
    r = await client.post("/v1/join/redeem", json={"code": code, "name": "dave"})
    assert r.status_code == 403

    code, inv = invite_codes.mint(store, room_id="x", room_name="build", role="member",
                                  issuer="arav", ttl_s=3600)
    with sqlite3.connect(store._path) as db:
        db.execute("UPDATE invites SET expires_at = ? WHERE id = ?", (time.time() - 1, inv.id))
    r = await client.post("/v1/join/redeem", json={"code": code, "name": "erin"})
    assert r.status_code == 403
    assert (await client.post("/v1/join/redeem",
                              json={"code": "qi_" + "x" * 30, "name": "fay"})).status_code == 403


async def test_a_taken_name_does_not_spend_the_use(client: AsyncClient) -> None:
    payload = await _mint(client)
    app.state.participant_keys.claim("bob")
    taken = await client.post("/v1/join/redeem", json={"code": payload["c"], "name": "bob"})
    assert taken.status_code == 409
    ok = await client.post("/v1/join/redeem", json={"code": payload["c"], "name": "bobby"})
    assert ok.status_code == 200


def test_two_redeems_racing_for_the_last_use_one_wins(tmp_path) -> None:
    store = ParticipantKeyStore(tmp_path / "relay")
    code, _ = invite_codes.mint(store, room_id="x", room_name="r", role="member",
                                issuer="arav", ttl_s=3600, max_uses=1)
    results: list[str] = []
    gate = threading.Barrier(8)

    def go(i: int) -> None:
        gate.wait()
        try:
            invite_codes.redeem(store, code, f"p{i}")
            results.append("ok")
        except invite_codes.InviteError as exc:
            results.append(str(exc.status))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("403") == 7


def test_old_claims_migrate_unclassified_not_human(tmp_path) -> None:
    """Codex on 45e1f921: existing claims include daemon keys, so they must not
    become human (approval authority) by migration."""
    folder = tmp_path / "relay"
    folder.mkdir()
    with sqlite3.connect(folder / "participant_keys.db") as db:
        db.execute("CREATE TABLE claims (name TEXT PRIMARY KEY, key_hash TEXT NOT NULL, "
                   "created_at REAL NOT NULL, claim_id TEXT)")
        db.execute("INSERT INTO claims VALUES ('arav-codex', 'h', 0, 'g1')")
    store = ParticipantKeyStore(folder)
    info = store.info("arav-codex")
    assert info["kind"] == "unclassified" and info["owner"] is None
    assert info["claim_id"] == "g1"  # the generation survives: tokens stay valid


def test_only_member_invites_until_observers_are_enforced(tmp_path) -> None:
    store = ParticipantKeyStore(tmp_path / "relay")
    with pytest.raises(ValueError):
        invite_codes.mint(store, room_id="x", room_name="r", role="observer",
                          issuer="arav", ttl_s=60)
    with pytest.raises(ValueError):
        invite_codes.mint(store, room_id="x", room_name="r", role="member",
                          issuer="arav", ttl_s=60, max_uses=21)


async def test_the_old_unclaimed_join_path_is_gone_here(client: AsyncClient) -> None:
    rid = (await client.get("/rooms/build", headers=ADMIN)).json()["id"]
    token = app.state.invite_service.create_token(
        tenant_id="_legacy", room_id=rid, issuer="arav", role="member")
    r = await client.post("/invite/build/join", json={"participant": "mallory",
                                                      "token": token})
    assert r.status_code == 410
