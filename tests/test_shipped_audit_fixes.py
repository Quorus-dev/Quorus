"""Regression tests for the v0.5.0 shipped-package security audit (S1-S5)."""

import pytest
from fastapi.testclient import TestClient

from quorus.auth import tokens
from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Services read these at _reset_state(); a leaked DATABASE_URL from an
    # earlier test would point join codes at an uninitialised Postgres.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path)
    monkeypatch.setattr(
        tokens, "JWT_SECRET", "audit-dummy-signing-value-at-least-32-bytes"
    )
    yield TestClient(app)
    _reset_state()


def _identity(client, name):
    key = client.post(
        "/v1/auth/claim-name", headers=ADMIN, json={"name": name}
    ).json()["participant_key"]
    token = client.post("/v1/auth/token", json={"api_key": key}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _room_with_lock(client):
    alice, bob = _identity(client, "alice"), _identity(client, "bob")
    rid = client.post(
        "/rooms",
        headers=alice,
        json={"name": "private", "created_by": "alice", "private": True},
    ).json()["id"]
    assert client.post(
        f"/rooms/{rid}/join", headers=alice, json={"participant": "bob"}
    ).status_code == 200
    lock = client.post(
        f"/rooms/{rid}/lock",
        headers=alice,
        json={"file_path": "app.py", "claimed_by": "alice"},
    )
    assert lock.status_code == 200
    return rid, alice, bob, lock.json()["lock_token"]


# S1 ------------------------------------------------------------------------


def test_room_state_hides_lock_token_from_peers(client):
    rid, alice, bob, token = _room_with_lock(client)
    state = client.get(f"/rooms/{rid}/state", headers=bob).json()
    assert "lock_token" not in state["locked_files"]["app.py"]
    assert all("lock_token" not in t for t in state["claimed_tasks"])
    assert token not in str(state)
    # The holder still sees its own token.
    own = client.get(f"/rooms/{rid}/state", headers=alice).json()
    assert own["locked_files"]["app.py"]["lock_token"] == token


def test_peer_cannot_release_another_members_lock(client):
    rid, alice, bob, token = _room_with_lock(client)
    released = client.request(
        "DELETE", f"/rooms/{rid}/lock/app.py", headers=bob,
        json={"lock_token": token},
    )
    assert released.status_code == 403
    still = client.get(f"/rooms/{rid}/state", headers=alice).json()
    assert "app.py" in still["locked_files"]
    ok = client.request(
        "DELETE", f"/rooms/{rid}/lock/app.py", headers=alice,
        json={"lock_token": token},
    )
    assert ok.status_code == 200


# S2 ------------------------------------------------------------------------


def test_direct_clients_cannot_rotate_forwarded_ips_to_bypass_limits(
    client, monkeypatch
):
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    for i in range(100):
        r = client.get(
            "/v1/join/resolve/ZZZZ-ZZZZ",
            headers={"X-Forwarded-For": f"198.51.100.{i}"},
        )
        assert r.status_code in (404, 429)
    r = client.get(
        "/v1/join/resolve/ZZZZ-ZZZZ", headers={"X-Forwarded-For": "203.0.113.1"}
    )
    assert r.status_code == 429


def test_forwarded_ip_used_only_behind_a_configured_proxy(monkeypatch):
    from types import SimpleNamespace

    from quorus.config import get_real_ip

    req = SimpleNamespace(
        headers={"X-Forwarded-For": "203.0.113.9"},
        client=SimpleNamespace(host="10.0.0.5"),
    )
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    assert get_real_ip(req) == "10.0.0.5"
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    assert get_real_ip(req) == "203.0.113.9"


# S3 ------------------------------------------------------------------------


async def _call_tool(monkeypatch, post_side_effect=None, post_result=None):
    from unittest.mock import AsyncMock, MagicMock

    import httpx
    from mcp import types
    from quorus_mcp import server, tools

    monkeypatch.setattr(server, "API_KEY", "")
    monkeypatch.setattr(server, "RELAY_SECRET", "audit-dummy")
    monkeypatch.setattr(server, "_remember_session", AsyncMock())
    monkeypatch.setattr(tools, "_audit_tool_call", AsyncMock())
    mock_client = AsyncMock()
    if post_side_effect is not None:
        mock_client.post.side_effect = post_side_effect
    else:
        resp = MagicMock(spec=httpx.Response)
        resp.json.return_value = post_result
        resp.raise_for_status.return_value = None
        mock_client.post.return_value = resp
    monkeypatch.setattr(server, "_get_http_client", lambda: mock_client)
    request = types.CallToolRequest(
        params=types.CallToolRequestParams(
            name="send_message", arguments={"to": "bob", "content": "test"}
        )
    )
    handler = server.mcp._mcp_server.request_handlers[types.CallToolRequest]
    return (await handler(request)).root


async def test_mcp_relay_failure_sets_error_flag(monkeypatch):
    import httpx

    result = await _call_tool(
        monkeypatch, post_side_effect=httpx.ConnectError("relay offline")
    )
    assert result.isError is True
    assert result.content[0].text.startswith("Error: Cannot reach relay")


async def test_mcp_success_is_not_flagged(monkeypatch):
    result = await _call_tool(monkeypatch, post_result={"id": "m1"})
    assert result.isError is False
    assert "Message sent" in result.content[0].text


# S4 ------------------------------------------------------------------------


def test_replay_masks_known_credentials_in_commit_metadata():
    from quorus_cli.replay_cmd import Commit, render

    dummy = "audit-dummy-credential"
    commit = Commit(
        "1234abcd", 1, "done", dummy, "human", f"1 file changed: {dummy}.txt"
    )
    msg = {"timestamp": "2026-10-18T00:00:00Z", "from_name": dummy, "content": "hi"}
    page = render(dummy, [msg], [commit], [dummy])
    assert dummy not in page


# S5 ------------------------------------------------------------------------


def _applescript_literal(script: str) -> str:
    """Decode the string literal from `tell ... to do script "<literal>"`."""
    import re

    body = re.fullmatch(r'tell application "Terminal" to do script "(.*)"', script)
    assert body, script
    return re.sub(r"\\(.)", r"\1", body.group(1))


@pytest.mark.parametrize(
    "dirname", ["repo; touch AUDIT_MARKER; #", 'say "hi" \\ $(id) `x`']
)
def test_spawn_codex_quotes_workspace_for_shell_and_applescript(
    tmp_path, monkeypatch, dirname
):
    import shlex

    from quorus_cli import cli

    workspace = tmp_path / dirname
    calls = []
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli, "_get_codex_runner_defaults", lambda: {})
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: calls.append(argv))
    cli._spawn_codex_agent("build", "alice-codex", workspace=workspace)
    assert calls[0][:2] == ["osascript", "-e"]
    argv = shlex.split(_applescript_literal(calls[0][2]))
    assert argv[:3] == ["cd", str(workspace), "&&"]
    assert argv[-2:] == ["--cwd", str(workspace)]
