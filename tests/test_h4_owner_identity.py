"""BUG H4 (0.6.3 hub audit): the human on the default local setup had only the relay
secret, so every social verb answered 403 "legacy auth not permitted". Fix, to
Codex's design: the wizard claims a normal participant key for the human (no takeover
on a collision), the hub and CLI use it as the everyday identity, and the secret stays
the operator credential for share, revoke, kick and destroy.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}


# ── relay: a human's key is a member identity, the secret stays the operator ──────


@pytest.fixture
async def client(tmp_path):
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    _reset_state()


async def _human(c: AsyncClient, name: str) -> dict:
    r = await c.post("/v1/auth/claim-name", json={"name": name}, headers=ADMIN)
    assert r.status_code == 200, r.text
    r = await c.post("/v1/auth/token", json={"api_key": r.json()["participant_key"]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_the_owners_key_votes_where_the_secret_could_not(client) -> None:
    alice = await _human(client, "alice")
    r = await client.post("/rooms", json={"name": "general", "created_by": "alice"}, headers=alice)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    body = {"actor": "alice", "room_id": rid,
            "payload": {"task_id": "t-1", "eta_seconds": 600, "scope": "audit"}}
    assert (await client.post("/v1/social/claim", json=body, headers=ADMIN)).status_code == 403
    r = await client.post("/v1/social/claim", json=body, headers=alice)
    assert r.status_code == 200, r.text
    assert r.json()["envelope"]["verb"] == "claim"


async def test_the_key_is_a_member_not_an_operator(client) -> None:
    alice = await _human(client, "alice")
    r = await client.post("/rooms", json={"name": "general", "created_by": "alice"}, headers=alice)
    assert r.status_code == 200
    # share (mint) stays an operator action: the secret may, the key may not.
    assert (await client.post("/v1/join/mint", json={"room": "general"},
                              headers=alice)).status_code == 403
    assert (await client.post("/v1/join/mint", json={"room": "general"},
                              headers=ADMIN)).status_code == 200


async def test_a_claimed_name_is_never_taken_over(client) -> None:
    await _human(client, "alice")
    r = await client.post("/v1/auth/claim-name", json={"name": "alice"}, headers=ADMIN)
    assert r.status_code == 409 and "release-name" in r.text


# ── wizard: the local path claims the key; a collision says how to recover ────────


class _Resp:
    def __init__(self, status: int, body: dict | None = None, text: str = ""):
        self.status_code, self._body, self.text = status, body or {}, text

    def json(self):
        return self._body


def _wizard(monkeypatch, tmp_path: Path, claim: _Resp, capsys):
    from quorus_tui import hub
    from rich.console import Console

    from quorus.runtime import local_relay

    # Everything the wizard could write goes under tmp_path: HOME as well as the
    # config dir (2026-10-20: this fixture once rewrote the overseer's real
    # ~/.quorus because the hub resolved its config path at import time).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / ".quorus"))
    monkeypatch.setenv("RELAY_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(hub, "_prompt_name", lambda console: "alice")
    monkeypatch.setattr(hub.Prompt, "ask", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(local_relay, "ensure_local_relay", lambda url, secret: (True, ""))
    calls: list[dict] = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return claim

    monkeypatch.setattr(hub.httpx, "post", fake_post)
    with open(tmp_path / "out.txt", "w") as fh:
        profile = hub._first_launch_setup(Console(file=fh, width=400))
    out = (tmp_path / "out.txt").read_text()
    # What the wizard really saved, wherever ConfigManager put it under tmp_path.
    saved = [{"name": d["instance_name"], "secret": d.get("relay_secret", ""),
              "api_key": d.get("api_key", "")}
             for f in sorted((tmp_path / ".quorus").rglob("*.json"))
             for d in [json.loads(f.read_text())]
             if isinstance(d, dict) and "instance_name" in d]
    return profile, calls, out, saved


def test_the_wizards_local_path_gives_the_human_a_participant_key(monkeypatch, tmp_path, capsys):
    profile, calls, _, saved = _wizard(
        monkeypatch, tmp_path, _Resp(200, {"name": "alice", "participant_key": "quorus_pk_x"}),
        capsys)
    [call] = calls
    assert call["url"].endswith("/v1/auth/claim-name") and call["json"] == {"name": "alice"}
    assert call["headers"]["Authorization"].startswith("Bearer ") and \
        call["headers"]["Authorization"] != "Bearer "
    assert profile["api_key"] == "quorus_pk_x" and profile["relay_secret"]
    [cfg] = saved
    assert cfg == {"name": "alice", "secret": profile["relay_secret"], "api_key": "quorus_pk_x"}


def test_a_taken_name_is_explained_not_taken_over(monkeypatch, tmp_path, capsys):
    profile, _, out, saved = _wizard(monkeypatch, tmp_path, _Resp(409, text="claimed"), capsys)
    assert profile["api_key"] == "" and profile["relay_secret"] and saved[0]["api_key"] == ""
    assert "already claimed" in out and "quorus release-name alice" in out


# ── hub session: the key is the identity, the secret the operator credential ─────


def test_session_credentials_split_identity_from_operator():
    from quorus_tui import hub

    assert hub.session_credentials({"api_key": "quorus_pk_x", "relay_secret": "s"}) == \
        ("quorus_pk_x", "s")
    assert hub.session_credentials({"relay_secret": "s"}) == ("s", "s")      # pre-0.6.4 setup
    assert hub.session_credentials({"api_key": "quorus_pk_x"}) == ("quorus_pk_x", "")  # invitee
    # Codex, review of 2c0bc92a: a hosted login never falls back to a stale dev secret,
    # not even for admin actions; the hosted relay would refuse it.
    hosted = {"api_key": "mct_h", "relay_secret": "stale"}
    assert hub.session_credentials(hosted) == ("mct_h", "")


def test_the_hub_mints_and_destroys_with_the_operator_credential(monkeypatch):
    from quorus_tui import hub

    seen: list[dict] = []

    class _C:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            return {"code": "ABCD-EFGH", "install_url": "http://r/i.sh"}

        def raise_for_status(self):
            return None

    def fake_post(url, **kw):
        seen.append(kw)
        return _C(200)

    monkeypatch.setattr(hub.httpx, "post", fake_post)
    monkeypatch.setattr(hub, "_ADMIN_SECRET", "operator-secret")
    hub._mint_join_token("http://r", "identity-jwt", "general")
    assert seen and seen[-1]["headers"]["Authorization"] == "Bearer operator-secret"
    monkeypatch.setattr(hub, "_ADMIN_SECRET", "")
    hub._mint_join_token("http://r", "identity-jwt", "general")
    # an invitee has no operator secret: the key goes and the relay answers 403
    assert seen[-1]["headers"]["Authorization"] == "Bearer identity-jwt"


# ── CLI: admin commands send the operator credential, everything else the key ───


def test_cli_admin_headers_prefer_the_relay_secret(monkeypatch):
    from quorus_cli import cli

    monkeypatch.setattr(cli, "RELAY_SECRET", "s")
    monkeypatch.setattr(cli, "API_KEY", "quorus_pk_x")
    monkeypatch.setattr(cli, "_cached_jwt", "jwt-for-key")
    assert cli._admin_headers() == {"Authorization": "Bearer s"}
    assert cli._auth_headers() == {"Authorization": "Bearer jwt-for-key"}
    monkeypatch.setattr(cli, "RELAY_SECRET", "")
    assert cli._admin_headers() == {"Authorization": "Bearer jwt-for-key"}
    # A hosted `mct_` login with a stale dev secret in the profile: the key's JWT
    # is the admin credential, never the secret (Codex, review of 2c0bc92a).
    monkeypatch.setattr(cli, "RELAY_SECRET", "quorus-dev")
    monkeypatch.setattr(cli, "API_KEY", "mct_hosted")
    monkeypatch.setattr(cli, "_cached_jwt", "jwt-for-hosted")
    assert cli._admin_headers() == {"Authorization": "Bearer jwt-for-hosted"}


def test_whoami_says_which_credential_signs_in(monkeypatch, capsys):
    from quorus_cli import cli

    class _PM:
        def current(self):
            return "default"

        def list(self):
            return ["default"]

        def get(self, slug):
            return {"instance_name": "alice", "relay_url": "http://r",
                    "api_key": "quorus_pk_x", "relay_secret": "s"}

    import quorus.profiles as profiles

    monkeypatch.setattr(profiles, "ProfileManager", _PM)
    cli._cmd_whoami(SimpleNamespace(workspace=None))
    out = capsys.readouterr().out
    assert "participant key (your identity)" in out and "relay secret (operator" in out
    assert "quorus_pk_x" not in out and "Bearer" not in out
