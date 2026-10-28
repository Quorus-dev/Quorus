"""A shell-wide API key on a relay with no database (0.7.13).

Arav's shell exported QUORUS_API_KEY. Every MCP tool tried API-key auth, the
local relay (relay secret, no database) answered 500, and every tool failed
with nothing saying the key came from the environment. The relay now answers
401 and says it has no API-key accounts; the MCP server and CLI then use the
relay secret saved for that same relay, or say which env var to unset.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from quorus.auth import tokens
from quorus.auth.participant_keys import ParticipantKeyStore, load_or_create_signing_key
from quorus.http_hints import AUTH_MODE_HEADER, ApiKeyRejected, key_auth_unavailable
from quorus.relay import _reset_state, app

SECRET = "test-secret"  # conftest's RELAY_SECRET
KEY = "mct_0123abcd_" + "ab" * 20
_ENV = ("QUORUS_RELAY_URL", "QUORUS_RELAY_SECRET", "QUORUS_API_KEY", "QUORUS_INSTANCE_NAME",
        "QUORUS_PROFILE", "RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME",
        "MCP_TUNNEL_CONFIG_DIR")


@pytest.fixture
def no_db(tmp_path: Path, monkeypatch):
    from quorus.storage import postgres

    _reset_state()
    monkeypatch.setattr(postgres, "_session_factory", None)
    monkeypatch.setattr(tokens, "JWT_SECRET", load_or_create_signing_key(tmp_path))
    app.state.participant_keys = ParticipantKeyStore(tmp_path)
    yield
    _reset_state()


# ── relay ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("call", [
    lambda c: c.post("/v1/auth/token", json={"api_key": KEY}),
    lambda c: c.post("/v1/auth/register-agent", json={"suffix": "claude"},
                     headers={"Authorization": f"Bearer {KEY}"}),
])
def test_a_key_on_a_relay_without_a_database_is_401_not_500(no_db, call) -> None:
    resp = call(TestClient(app, raise_server_exceptions=False))
    assert resp.status_code == 401, resp.text
    assert resp.headers[AUTH_MODE_HEADER] == "secret"
    detail = resp.json()["detail"]
    assert "no API-key accounts" in detail and "QUORUS_API_KEY" in detail
    assert KEY not in resp.text
    assert key_auth_unavailable(resp)


def test_signup_on_a_relay_without_a_database_is_400_not_500(no_db) -> None:
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/v1/auth/signup", json={"name": "bob", "workspace": "bob"})
    assert resp.status_code == 400, resp.text
    assert "invite code" in resp.json()["detail"]


def test_a_wrong_key_is_not_mistaken_for_a_relay_without_accounts() -> None:
    req = httpx.Request("POST", "http://r.test/v1/auth/token")
    bad = httpx.Response(401, json={"detail": "Invalid or revoked API key"}, request=req)
    assert not key_auth_unavailable(bad)
    old_relay = httpx.Response(401, request=req, json={
        "detail": "This relay has no API-key accounts (it runs without a database)."})
    assert key_auth_unavailable(old_relay)  # 0.7.10-0.7.12 relays: detail only


# ── MCP server ──────────────────────────────────────────────────────────────


@pytest.fixture
def mcp(monkeypatch):
    from quorus_mcp import server

    async def _wire(*, secret: str, relay_app=app):
        client = AsyncClient(transport=ASGITransport(app=relay_app), base_url="http://relay.test")
        monkeypatch.setattr(server, "_http_client", client)
        monkeypatch.setattr(server, "SETUP_PROBLEM", "")
        monkeypatch.setattr(server, "RELAY_URL", "http://relay.test")
        monkeypatch.setattr(server, "RELAY_SECRET", secret)
        monkeypatch.setattr(server, "API_KEY", KEY)
        monkeypatch.setattr(server, "API_KEY_FROM", "QUORUS_API_KEY")
        monkeypatch.setattr(server, "_cached_jwt", None)
        return server, client
    return _wire


async def test_mcp_falls_back_to_the_relay_secret_when_no_key_can_sign_in(no_db, mcp) -> None:
    server, client = await mcp(secret=SECRET)
    headers = await server._auth_headers_async()
    assert headers == {"Authorization": f"Bearer {SECRET}"}
    assert server.API_KEY == "" and server._cached_jwt is None
    assert (await client.get("/rooms", headers=headers)).status_code == 200
    await client.aclose()


async def test_mcp_without_a_secret_says_which_env_var_to_unset(no_db, mcp) -> None:
    server, client = await mcp(secret="")
    with pytest.raises(ApiKeyRejected) as exc:
        await server._get_bearer_token()
    msg = server._relay_error_message(exc.value)
    assert "QUORUS_API_KEY environment variable" in msg and "unset QUORUS_API_KEY" in msg
    assert 'QUORUS_API_KEY=""' in msg and KEY not in msg
    assert server.API_KEY == KEY  # nothing switched
    await client.aclose()


async def test_mcp_does_not_fall_back_when_the_key_is_simply_wrong(mcp) -> None:
    """Fail closed: a refused key on a relay that HAS accounts is an error,
    never a silent switch to the relay secret."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    db_relay = FastAPI()

    @db_relay.post("/v1/auth/token")
    async def _token():
        return JSONResponse({"detail": "Invalid or revoked API key"}, status_code=401)

    server, client = await mcp(secret=SECRET, relay_app=db_relay)
    with pytest.raises(ApiKeyRejected) as exc:
        await server._get_bearer_token()
    assert "unset QUORUS_API_KEY" in str(exc.value)
    assert server.API_KEY == KEY and SECRET not in str(exc.value)
    await client.aclose()


def test_runtime_records_that_the_key_came_from_the_env(tmp_path, monkeypatch) -> None:
    from quorus_mcp import runtime

    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps(
        {"instance_name": "alice", "relay_url": "http://saved.test", "relay_secret": "s3"}))
    assert runtime.load_runtime_config()["api_key_from"] == ""
    monkeypatch.setenv("QUORUS_API_KEY", KEY)
    settings = runtime.load_runtime_config()
    assert settings["api_key_from"] == "QUORUS_API_KEY"
    assert settings["relay_secret"] == "s3"  # still there to fall back to


# ── CLI ─────────────────────────────────────────────────────────────────────


def _token_reply(status: int, **kw):
    req = httpx.Request("POST", "http://relay.test/v1/auth/token")
    return lambda *a, **k: httpx.Response(status, request=req, **kw)


def test_cli_falls_back_to_the_relay_secret(monkeypatch, capsys) -> None:
    from quorus_cli import cli

    monkeypatch.setattr(cli, "API_KEY", KEY)
    monkeypatch.setattr(cli, "RELAY_SECRET", SECRET)
    monkeypatch.setattr(cli, "_cached_jwt", None)
    monkeypatch.setattr(cli.httpx, "post", _token_reply(
        401, json={"detail": "no API-key accounts"}, headers={AUTH_MODE_HEADER: "secret"}))
    assert cli._auth_headers() == {"Authorization": f"Bearer {SECRET}"}
    assert cli.API_KEY == ""
    assert SECRET not in capsys.readouterr().err


def test_cli_explains_an_env_key_it_cannot_use(monkeypatch, capsys) -> None:
    from quorus_cli import cli

    monkeypatch.setattr(cli, "API_KEY", KEY)
    monkeypatch.setattr(cli, "RELAY_SECRET", "")
    monkeypatch.setattr(cli, "_cached_jwt", None)
    monkeypatch.setitem(cli._config, "api_key_from_env", True)
    monkeypatch.setattr(cli.httpx, "post", _token_reply(500, text="Internal Server Error"))
    with pytest.raises(ApiKeyRejected) as exc:
        cli._auth_headers()
    assert cli._explain_http_error(exc.value) == 3
    out = capsys.readouterr()
    text = " ".join((out.out + out.err).split())
    assert "API_KEY environment variable" in text and "unset API_KEY" in text
    assert KEY not in text
