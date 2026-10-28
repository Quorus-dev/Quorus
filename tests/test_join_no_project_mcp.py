"""`quorus join` never writes a .mcp.json into the folder it runs from (0.7.13).

Arav ran `quorus join <code> --name X --relay URL` and it left a project
.mcp.json in the cwd with QUORUS_INSTANCE_NAME=X (older versions also put the
relay secret there). Every Claude Code session later opened in that folder was
silently renamed X. Join writes only user-scope config under ~/.quorus.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}
_ENV = ("QUORUS_RELAY_URL", "QUORUS_RELAY_SECRET", "QUORUS_API_KEY", "QUORUS_INSTANCE_NAME",
        "QUORUS_PROFILE", "RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME",
        "MCP_TUNNEL_CONFIG_DIR")


@pytest.fixture
async def relay(tmp_path):
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/rooms", json={"name": "build", "created_by": "arav"}, headers=ADMIN)
        assert r.status_code == 200, r.text
        yield c
    _reset_state()


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A clean HOME, ~/.quorus, and an empty project folder as the cwd, with
    the CLI's HTTP routed into the in-process relay."""
    from quorus_cli import cli

    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(home / ".quorus"))
    monkeypatch.chdir(project)
    monkeypatch.setattr("shutil.which", lambda _name: None)  # no codex on PATH
    tc = TestClient(app)

    def route(method):
        def call(url, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            return getattr(tc, method)(urlsplit(url).path, **kw)
        return call
    monkeypatch.setattr(cli.httpx, "post", route("post"))
    monkeypatch.setattr(cli.httpx, "get", route("get"))
    return cli, home, project


async def test_join_with_no_mcp_client_leaves_no_project_mcp_json(relay, machine) -> None:
    cli, home, project = machine
    r = await relay.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    args = argparse.Namespace(token=r.json()["code"], name="frank",
                              relay_url="http://192.168.1.20:8080")
    await asyncio.to_thread(cli._cmd_join, args)

    assert list(project.iterdir()) == []  # nothing at all in the cwd
    config = json.loads((home / ".quorus" / "config.json").read_text())
    assert config["instance_name"] == "frank" and config["api_key"].startswith("quorus_pk_")
    assert "test-secret" not in json.dumps(config)
    assert "frank" in (await relay.get("/rooms/build", headers=ADMIN)).json()["members"]


async def test_join_with_claude_code_registers_user_scope_only(relay, machine) -> None:
    cli, home, project = machine
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text("{}")
    r = await relay.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    args = argparse.Namespace(token=r.json()["code"], name="gina",
                              relay_url="http://192.168.1.20:8080")
    await asyncio.to_thread(cli._cmd_join, args)

    assert list(project.iterdir()) == []
    entry = json.loads((home / ".claude" / "settings.json").read_text())["mcpServers"]["quorus"]
    assert "QUORUS_RELAY_SECRET" not in entry["env"]
    assert "test-secret" not in json.dumps(entry)


def test_init_keeps_its_project_fallback(machine, monkeypatch) -> None:
    """Only join changed: the shared helper still writes the secret-free
    project file when its caller asks for it (init)."""
    cli, _home, project = machine
    monkeypatch.setattr(cli, "_is_platform_installed", lambda _p: False)
    cli._register_mcp_everywhere(name="alice", relay_url="http://r.test", api_key="",
                                 mcp_command="quorus-mcp", mcp_args=[], ui=MagicMock())
    assert (project / ".mcp.json").exists()
    (project / ".mcp.json").unlink()
    cli._register_mcp_everywhere(name="alice", relay_url="http://r.test", api_key="",
                                 mcp_command="quorus-mcp", mcp_args=[], ui=MagicMock(),
                                 project_fallback=False)
    assert not (project / ".mcp.json").exists()
