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
from quorus.routes import join_redeem

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
            invite_codes.redeem(store, code, f"p{i}")  # returns (key, claim_id, invite)
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


async def test_an_invitee_cant_take_a_name_the_relay_already_knows(client) -> None:
    """Codex on 3bedbbea: "arav" never claimed a key (shared-secret owner), yet
    rooms grant access by name, so redeeming as "arav" used to get 200."""
    secret_room = await client.post("/rooms", json={"name": "private", "created_by": "arav"},
                                    headers=ADMIN)
    assert secret_room.status_code == 200, secret_room.text
    await client.post("/heartbeat", headers=ADMIN, json={"instance_name": "arav-codex"})
    for i, taken in enumerate(("arav", "Arav", "arav-codex")):
        payload = await _mint(client)
        r = await client.post("/v1/join/redeem", json={"code": payload["c"], "name": taken})
        assert r.status_code == 409, (taken, r.text)
        # the use wasn't spent: the same code still works for a fresh name
        r = await client.post("/v1/join/redeem", json={"code": payload["c"],
                                                       "name": f"guest{i}"})
        assert r.status_code == 200, r.text


def test_a_claimed_name_blocks_its_other_spellings(tmp_path) -> None:
    store = ParticipantKeyStore(tmp_path / "relay")
    code, _ = invite_codes.mint(store, room_id="x", room_name="r", role="member",
                                issuer="arav", ttl_s=3600, max_uses=2)
    invite_codes.redeem(store, code, "bob")
    with pytest.raises(invite_codes.InviteError) as exc:
        invite_codes.redeem(store, code, "BOB")
    assert exc.value.status == 409


def test_late_compensation_spares_a_newer_claim_and_runs_once(tmp_path) -> None:
    """Codex on 3bedbbea: undo deleted by name, so a delayed undo removed a
    replacement claim. It now matches the generation, and a repeat is a no-op."""
    store = ParticipantKeyStore(tmp_path / "relay")
    code, _ = invite_codes.mint(store, room_id="x", room_name="r", role="member",
                                issuer="arav", ttl_s=3600, max_uses=3)
    _, first, inv = invite_codes.redeem(store, code, "bob")
    with store._lock, store._connect() as db:  # bob released, someone reclaims it
        db.execute("DELETE FROM claims WHERE name = 'bob'")
    _, second, _ = invite_codes.redeem(store, code, "bob")
    assert first != second
    assert invite_codes.undo_redeem(store, inv.id, "bob", first) is False
    assert store.info("bob")["claim_id"] == second  # the newer claim survives
    uses = lambda: next(i["uses"] for i in invite_codes.list_invites(store))  # noqa: E731
    assert uses() == 2  # and no use came back for a claim it didn't remove
    assert invite_codes.undo_redeem(store, inv.id, "bob", second) is True
    assert invite_codes.undo_redeem(store, inv.id, "bob", second) is False
    assert uses() == 1 and store.info("bob") is None


async def test_a_room_created_mid_redeem_wins_over_the_invitee(client) -> None:
    """Codex on 8e6fa5c2: the known-names snapshot was stale. Create a room as
    'zed' between that read and the claim, and the invitee got zed's name."""
    payload = await _mint(client)
    stale = await join_redeem._known_names(_Req(app))  # read before 'zed' existed
    assert "zed" not in stale
    r = await client.post("/rooms", json={"name": "zeds-room", "created_by": "zed"},
                          headers=ADMIN)
    assert r.status_code == 200, r.text
    with pytest.raises(invite_codes.InviteError) as exc:
        invite_codes.redeem(app.state.participant_keys, payload["c"], "zed", stale)
    assert exc.value.status == 409


async def test_an_invitees_name_cant_be_granted_to_the_secret_holder(client) -> None:
    """The other order: the redeem commits first, then a room is created as
    that name with the shared secret. The grant is refused, so the invitee
    never inherits a room made for someone else."""
    payload = await _mint(client)
    r = await client.post("/v1/join/redeem", json={"code": payload["c"], "name": "dana"})
    assert r.status_code == 200, r.text
    key = r.json()["participant_key"]
    for call in (
        client.post("/rooms", json={"name": "secret", "created_by": "dana"}, headers=ADMIN),
        client.post("/rooms/build/join", json={"participant": "Dana"}, headers=ADMIN),
        client.post("/heartbeat", json={"instance_name": "dana"}, headers=ADMIN),
    ):
        resp = await call
        assert resp.status_code == 409, resp.text
    assert (await client.get("/rooms/secret", headers=ADMIN)).status_code == 404
    # The invitee acting as itself is unaffected.
    jwt = (await client.post("/v1/auth/token", json={"api_key": key})).json()["token"]
    me = {"Authorization": f"Bearer {jwt}"}
    r = await client.post("/rooms/build/messages", headers=me,
                          json={"from_name": "dana", "content": "hi"})
    assert r.status_code == 200, r.text


class _Req:
    def __init__(self, application) -> None:
        self.app = application


async def test_a_dm_to_a_new_name_reserves_it_before_delivery(client) -> None:
    """Codex on 6f87fb51: only the sender was reserved, so a DM to a name
    nobody held yet could be claimed by an invitee who then read it."""
    payload = await _mint(client)
    stale = await join_redeem._known_names(_Req(app))
    r = await client.post("/messages", headers=ADMIN,
                          json={"from_name": "arav", "to": "erin", "content": "private"})
    assert r.status_code == 200, r.text
    with pytest.raises(invite_codes.InviteError) as exc:
        invite_codes.redeem(app.state.participant_keys, payload["c"], "erin", stale)
    assert exc.value.status == 409


async def test_dms_still_reach_an_existing_invitee(client) -> None:
    payload = await _mint(client)
    assert (await client.post("/v1/join/redeem", json={"code": payload["c"],
                                                       "name": "dana"})).status_code == 200
    r = await client.post("/messages", headers=ADMIN,
                          json={"from_name": "arav", "to": "dana", "content": "welcome"})
    assert r.status_code == 200, r.text


def _cli_against_relay(monkeypatch, tmp_path):
    """Route the CLI's httpx calls into this relay app, and keep its config and
    MCP registrations in tmp_path (never the developer's real HOME)."""
    from urllib.parse import urlsplit

    from fastapi.testclient import TestClient
    from quorus_cli import cli

    tc = TestClient(app)

    def route(method):
        def call(url, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            return getattr(tc, method)(urlsplit(url).path, **kw)
        return call
    monkeypatch.setattr(cli.httpx, "post", route("post"))
    monkeypatch.setattr(cli.httpx, "get", route("get"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "joiner"))
    monkeypatch.setattr(cli, "_register_mcp_everywhere", lambda **_k: ([], {}))
    return cli


async def test_quorus_join_redeems_a_v2_code_into_its_own_key(client, monkeypatch,
                                                              tmp_path) -> None:
    """Part B: the joiner ends up with its own participant key in its config,
    the shared secret never reaches its disk, and it is in the room."""
    import json as _json

    payload = await _mint(client)
    cli = _cli_against_relay(monkeypatch, tmp_path)
    import asyncio as _asyncio

    await _asyncio.to_thread(cli._apply_join_payload, {**payload, "r": "http://t"}, "erin")
    config = _json.loads((tmp_path / "joiner" / "config.json").read_text())
    assert config["api_key"].startswith("quorus_pk_") and "relay_secret" not in config
    assert "test-secret" not in (tmp_path / "joiner" / "config.json").read_text()
    members = (await client.get("/rooms/build", headers=ADMIN)).json()["members"]
    assert "erin" in members


async def test_quorus_share_code_joins_end_to_end(client, monkeypatch, tmp_path) -> None:
    """The short code `quorus share` prints resolves to a v2 payload, and
    `quorus join CODE` turns it into the joiner's own key."""
    import argparse
    import asyncio as _asyncio
    import json as _json

    r = await client.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    cli = _cli_against_relay(monkeypatch, tmp_path)
    args = argparse.Namespace(token=r.json()["code"], name="frank", relay_url="http://t")
    await _asyncio.to_thread(cli._cmd_join, args)
    config = _json.loads((tmp_path / "joiner" / "config.json").read_text())
    assert config["api_key"].startswith("quorus_pk_") and "relay_secret" not in config
    members = (await client.get("/rooms/build", headers=ADMIN)).json()["members"]
    assert "frank" in members


async def test_quorus_join_explains_a_taken_name(client, monkeypatch, tmp_path, capsys) -> None:
    payload = await _mint(client)
    cli = _cli_against_relay(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli._apply_join_payload({**payload, "r": "http://t"}, "arav")  # the owner's name
    assert exc.value.code == 4
    assert not (tmp_path / "joiner" / "config.json").exists()
