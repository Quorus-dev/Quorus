"""`quorus room add <room> <member...>` (friction 8): the lead had to call the
relay's join API by hand with the secret to put an existing agent in a second
room. The relay still decides who may add; a refusal exits non-zero."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from quorus_cli import cli, room_add

from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}


@pytest.fixture
async def relay(tmp_path, monkeypatch):
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    headers = dict(ADMIN)
    monkeypatch.setattr(cli, "RELAY_URL", "http://t")
    monkeypatch.setattr(cli, "_auth_headers", lambda: headers)
    monkeypatch.setattr(cli, "_admin_headers", lambda: headers)  # room add sends this (H4)
    monkeypatch.setattr(cli, "_get_client", lambda: AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/rooms", json={"name": "build", "created_by": "alice"}, headers=ADMIN)
        assert r.status_code == 200, r.text
        yield c, headers
    _reset_state()


async def _roles(c: AsyncClient) -> dict:
    return (await c.get("/rooms/build", headers=ADMIN)).json()["member_roles"]


async def test_the_owner_adds_existing_agents_to_a_room(relay) -> None:
    c, _ = relay
    assert await room_add.add_members("build", ["alice-codex", "alice-claude",
                                                "alice-codex"]) == 0
    roles = await _roles(c)
    assert roles["alice-codex"] == "member" and roles["alice-claude"] == "member"


async def test_a_bad_name_says_what_is_wrong(relay, capsys) -> None:
    assert await room_add.add_members("build", ["no spaces allowed"]) == 1
    out = capsys.readouterr().out
    assert "@no spaces allowed" in out and "{'type'" not in out


async def test_a_missing_room_is_refused_with_a_next_step(relay, capsys) -> None:
    assert await room_add.add_members("nosuchroom", ["alice-codex"]) == 1
    assert "quorus rooms" in capsys.readouterr().out


async def test_a_member_cant_add_someone_else(relay, capsys) -> None:
    c, headers = relay
    r = await c.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    key = (await c.post("/v1/join/redeem", json={"code": payload["c"], "name": "bob"})
           ).json()["participant_key"]
    token = (await c.post("/v1/auth/token", json={"api_key": key})).json()["token"]
    headers.clear()
    headers["Authorization"] = f"Bearer {token}"
    assert await room_add.add_members("build", ["carol"]) == 1
    assert "creator or admin" in capsys.readouterr().out
    assert "carol" not in await _roles(c)


def test_cmd_room_add_exits_nonzero_when_any_is_refused(monkeypatch) -> None:
    async def fake(room, members):
        return 1

    monkeypatch.setattr(room_add, "add_members", fake)
    args = SimpleNamespace(room="build", members=["a", "b"])
    with pytest.raises(SystemExit) as exc:
        room_add.cmd_room_add(args)
    assert exc.value.code == 1


def test_the_cli_exposes_room_add() -> None:
    out = subprocess.run(
        [sys.executable, "-c", "import sys; from quorus_cli.cli import main; "
         "sys.argv=['quorus','room','add','--help']; main()"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-400:]
    for word in ("room", "members"):
        assert word in out.stdout


async def _invitee(c: AsyncClient, name: str, **mint) -> dict:
    r = await c.post("/v1/join/mint", json={"room": "build", **mint}, headers=ADMIN)
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    key = (await c.post("/v1/join/redeem", json={"code": payload["c"], "name": name})
           ).json()["participant_key"]
    token = (await c.post("/v1/auth/token", json={"api_key": key})).json()["token"]
    return {"Authorization": f"Bearer {token}"}


async def test_the_creator_re_adds_a_kicked_invitee(relay) -> None:
    """Fable's bug bash: re-adding kicked bob got 409 "the name 'bob' belongs to an
    invited member", so the only way back was a new invite. Putting him back in the
    room his own invite was for is allowed; posting as him stays refused."""
    c, _ = relay
    bob = await _invitee(c, "bob")
    r = await c.post("/rooms/build/kick", json={"participant": "bob", "requested_by": "alice"},
                     headers=ADMIN)
    assert r.status_code == 200, r.text
    assert (await c.get("/rooms/build/history", headers=bob)).status_code == 403
    assert await room_add.add_members("build", ["bob"]) == 0
    assert (await c.get("/rooms/build/history", headers=bob)).status_code == 200
    forged = await c.post("/rooms/build/messages", headers=ADMIN,
                          json={"from_name": "bob", "content": "hi, it's bob"})
    assert forged.status_code == 409, forged.text


async def test_adding_an_unclaimed_name_still_reserves_it(relay) -> None:
    """The secret holder adds "zed": no later invite can claim zed and read the room."""
    c, _ = relay
    assert await room_add.add_members("build", ["zed"]) == 0
    r = await c.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    taken = await c.post("/v1/join/redeem", json={"code": payload["c"], "name": "zed"})
    assert taken.status_code == 409, taken.text


async def test_an_invitee_isnt_granted_a_room_their_invite_wasnt_for(relay, capsys) -> None:
    """Codex on 8e6fa5c2: a grant meant for someone else must never land on an
    invitee's name. Only the room their own invite was for readmits them."""
    c, _ = relay
    await _invitee(c, "bob")
    r = await c.post("/rooms", json={"name": "ops", "created_by": "alice"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert await room_add.add_members("ops", ["bob"]) == 1
    assert "belongs to an invited member" in capsys.readouterr().out


async def test_a_readmitted_observer_is_still_an_observer(relay) -> None:
    c, _ = relay
    await _invitee(c, "dana", role="observer")
    await c.post("/rooms/build/kick", json={"participant": "dana", "requested_by": "alice"},
                 headers=ADMIN)
    assert await room_add.add_members("build", ["dana"]) == 0
    assert (await _roles(c))["dana"] == "observer"


async def test_a_revoked_invitee_needs_a_new_invite(relay) -> None:
    c, _ = relay
    r = await c.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    inv_id = r.json()["invite_id"]
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    await c.post("/v1/join/redeem", json={"code": payload["c"], "name": "erin"})
    revoked = await c.post(f"/v1/join/invites/{inv_id}/revoke", headers=ADMIN)
    assert revoked.status_code == 200, revoked.text
    assert await room_add.add_members("build", ["erin"]) == 1
    assert "erin" not in await _roles(c)


async def test_a_revoke_during_a_readmit_takes_the_member_back_out(relay, monkeypatch) -> None:
    """Codex on e578e184: pause the join after the readmit check passes, revoke the
    invite, resume. The join must not land: the route checks again after inserting,
    as join_redeem does, and takes the member back out."""
    import asyncio

    from quorus.routes import rooms

    c, _ = relay
    r = await c.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    inv_id = r.json()["invite_id"]
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    key = (await c.post("/v1/join/redeem", json={"code": payload["c"], "name": "bob"})
           ).json()["participant_key"]
    token = (await c.post("/v1/auth/token", json={"api_key": key})).json()["token"]
    bob = {"Authorization": f"Bearer {token}"}
    await c.post("/rooms/build/kick", json={"participant": "bob", "requested_by": "alice"},
                 headers=ADMIN)

    real, checked, resume = rooms._readmit_role, asyncio.Event(), asyncio.Event()

    async def paused(*a):
        role = await real(*a)
        if not checked.is_set():
            checked.set()
            await resume.wait()
        return role

    monkeypatch.setattr(rooms, "_readmit_role", paused)
    join = asyncio.create_task(c.post("/rooms/build/join", json={"participant": "bob"},
                                      headers=ADMIN))
    await checked.wait()
    assert (await c.post(f"/v1/join/invites/{inv_id}/revoke", headers=ADMIN)).status_code == 200
    resume.set()
    assert (await join).status_code == 403
    assert "bob" not in await _roles(c)
    assert (await c.get("/rooms/build/history", headers=bob)).status_code == 403
