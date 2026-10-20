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
