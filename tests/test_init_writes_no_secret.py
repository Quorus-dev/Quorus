"""The relay secret stays in the 0600 profile, never in an MCP config.

0.5.1 agent-path check: with no MCP client installed, `quorus init --secret`
wrote ./.mcp.json in the working directory with QUORUS_RELAY_SECRET in
plaintext, mode 0644: a file people commit. quorus-mcp reads the secret from
the saved profile for the same relay, so no written config needs it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

RELAY = "http://127.0.0.1:18770"
SECRET = "init-secret-4c1e9b"
_ENV = ("QUORUS_RELAY_URL", "QUORUS_RELAY_SECRET", "QUORUS_API_KEY", "QUORUS_INSTANCE_NAME",
        "QUORUS_PROFILE", "RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME",
        "MCP_TUNNEL_CONFIG_DIR")


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fresh home and an empty project directory as the cwd."""
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(home / ".quorus"))
    monkeypatch.chdir(project)
    monkeypatch.setattr("quorus.cli.shutil.which", lambda _: None)
    monkeypatch.setattr("quorus.cli.httpx.get", lambda *a, **kw: MagicMock(status_code=200))
    return home, project


def _init():
    from quorus_cli.cli import _cmd_init

    args = MagicMock()
    args.name, args.relay_url, args.secret, args.api_key = "alice", RELAY, SECRET, None
    _cmd_init(args)


def _files_holding(root: Path, needle: str) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and needle in p.read_text(errors="ignore")]


def _mcp_env(path: Path) -> dict:
    return json.loads(path.read_text())["mcpServers"]["quorus"]["env"]


def _runtime_with(env: dict, monkeypatch) -> dict:
    """What quorus-mcp resolves when a client starts it with *env*."""
    from quorus_mcp.runtime import load_runtime_config

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load_runtime_config()


def test_no_client_fallback_mcp_json_has_no_secret_and_still_works(machine, monkeypatch):
    home, project = machine
    monkeypatch.setattr("quorus.cli._is_platform_installed", lambda p: False)
    _init()
    env = _mcp_env(project / ".mcp.json")
    assert env == {"QUORUS_RELAY_URL": RELAY, "QUORUS_INSTANCE_NAME": "alice"}
    assert _files_holding(project, SECRET) == []
    # Only the profile under ~/.quorus holds it, at 0600.
    holders = _files_holding(home, SECRET)
    assert holders and all(p.is_relative_to(home / ".quorus") for p in holders)
    assert all(os.stat(p).st_mode & 0o077 == 0 for p in holders)
    assert _runtime_with(env, monkeypatch)["relay_secret"] == SECRET


def test_client_configs_init_writes_have_no_secret(machine, monkeypatch):
    home, project = machine
    (home / ".claude.json").write_text("{}")
    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text("{}")
    monkeypatch.setattr("quorus.cli._is_platform_installed",
                        lambda p: p in ("claude", "cursor"))
    _init()
    env = _mcp_env(home / ".claude.json")
    assert "QUORUS_RELAY_SECRET" not in env and "QUORUS_RELAY_SECRET" not in _mcp_env(
        home / ".cursor" / "mcp.json")
    assert [p for p in _files_holding(home, SECRET)
            if not p.is_relative_to(home / ".quorus")] == []
    assert not (project / ".mcp.json").exists()
    assert _runtime_with(env, monkeypatch)["relay_secret"] == SECRET


def test_key_fallback_pins_the_agent_profile_instead_of_holding_the_key(machine, monkeypatch):
    from quorus_cli import cli

    home, project = machine
    monkeypatch.setattr("quorus.cli._is_platform_installed", lambda p: False)
    cli._register_mcp_everywhere(name="alice", relay_url=RELAY, api_key="mct_alice_key",
                                 mcp_command="quorus-mcp", mcp_args=[], ui=MagicMock(),
                                 profile="default")
    env = _mcp_env(project / ".mcp.json")
    assert env == {"QUORUS_RELAY_URL": RELAY, "QUORUS_INSTANCE_NAME": "alice",
                   "QUORUS_PROFILE": "default"}
    # init leaves 'human' current; the pin still finds the agent's key.
    cfg = home / ".quorus"
    (cfg / "profiles").mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"current": "human",
                                                 "profiles": ["default", "human"]}))
    (cfg / "profiles" / "default.json").write_text(json.dumps(
        {"relay_url": RELAY, "instance_name": "alice", "api_key": "mct_alice_key"}))
    (cfg / "profiles" / "human.json").write_text(json.dumps(
        {"relay_url": RELAY, "instance_name": "alice-human", "api_key": "mct_human_key"}))
    assert _runtime_with(env, monkeypatch)["api_key"] == "mct_alice_key"


def test_spawned_agent_workspace_has_no_secret(machine, monkeypatch):
    from quorus_cli import cli

    home, _project = machine
    client = AsyncMock()
    client.post.side_effect = RuntimeError("offline")
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    monkeypatch.setattr(cli, "_open_in_terminal", lambda cmd: None)
    cli._spawn_agent("dev", "alice-agent", RELAY, SECRET)
    workspace = home / "quorus-agents" / "alice-agent"
    assert "RELAY_SECRET" not in _mcp_env(workspace / ".mcp.json")
    assert [p for p in workspace.glob("*") if p.is_file()
            and SECRET in p.read_text(errors="ignore")] == []
