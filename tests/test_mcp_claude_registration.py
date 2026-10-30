"""Synthetic HOME only: Claude setup never renames another session."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest
from quorus_cli import mcp_claude as claude
from quorus_cli import mcp_config_io as config_io
from quorus_cli.mcp_writers import McpEnv, register_all, register_cursor, register_one


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / ".quorus"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def env():
    return McpEnv(command="quorus-mcp", args=[], relay_url="https://new.test",
                  api_key="synthetic-key", instance_name_base="new")


@pytest.fixture
def code_cli(home, monkeypatch):
    """Model the vendor CLI preserving its live state; never launch a real client."""
    calls = []
    monkeypatch.setattr("shutil.which", lambda key: "/fake/claude" if key == "claude" else None)
    path = home / ".claude.json"

    def run(args, **kwargs):
        calls.append(args)
        assert args[:2] == ["/fake/claude", "mcp"]
        assert args[-2:] == ["-s", "user"]
        if path.exists():
            assert list(home.glob(".claude.json.bak-quorus-*"))
        data = json.loads(path.read_text()) if path.exists() else {}
        servers = data.setdefault("mcpServers", {})
        if args[2] == "remove":
            servers.pop("quorus", None)
        else:
            assert args[2:4] == ["add-json", "quorus"]
            servers["quorus"] = json.loads(args[4])
        # Simulate a live state update between calls, which must survive.
        data["liveCounter"] = data.get("liveCounter", 0) + 1
        path.write_text(json.dumps(data))
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(claude.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("os_name,suffix", [
    ("darwin", "Library/Application Support/Claude/claude_desktop_config.json"),
    ("win32", "AppData/Roaming/Claude/claude_desktop_config.json"),
    ("linux", ".config/Claude/claude_desktop_config.json"),
])
def test_desktop_uses_its_own_file_and_preserves_code_state(home, env, monkeypatch,
                                                         os_name, suffix):
    monkeypatch.setattr(claude.sys, "platform", os_name)
    live = home / ".claude.json"
    before = b'{"oauthAccount":{"synthetic":true},"projects":{"x":{"counter":3}}}'
    live.write_bytes(before)
    path = home / suffix
    path.parent.mkdir(parents=True)
    original = {"preferences": {"theme": "dark"}, "mcpServers": {"other": {"command": "x"}}}
    path.write_text(json.dumps(original))
    result = claude.register_claude_desktop(env)
    assert result.ok and result.path == path
    assert live.read_bytes() == before
    data = json.loads(path.read_text())
    assert data["preferences"] == original["preferences"]
    assert data["mcpServers"]["other"] == original["mcpServers"]["other"]
    backups = list(path.parent.glob(path.name + ".bak-quorus-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original
    assert backups[0].stat().st_mode & 0o077 == 0


def test_code_uses_supported_cli_and_keeps_unrelated_keys(home, env, code_cli):
    path = home / ".claude.json"
    original = {"projects": {"repo": {"lastCost": 8}}, "mcpServers": {"other": {}}}
    path.write_text(json.dumps(original))
    result = claude.register_claude_code(env)
    assert result.ok and len(code_cli) == 1
    data = json.loads(path.read_text())
    assert data["projects"] == original["projects"] and data["mcpServers"]["other"] == {}
    assert data["liveCounter"] == 1
    assert data["mcpServers"]["quorus"]["env"]["QUORUS_INSTANCE_NAME"] == "new-claude"
    assert not (home / ".claude" / "settings.json").exists()


@pytest.mark.parametrize("field,value", [("QUORUS_INSTANCE_NAME", "old-claude"),
                                        ("QUORUS_RELAY_URL", "https://old.test")])
def test_code_keeps_a_different_identity_unless_forced(home, env, code_cli, field, value):
    path = home / ".claude.json"
    entry = {"command": "quorus-mcp", "env": env.env_block("claude")}
    entry["env"][field] = value
    original = json.dumps({"mcpServers": {"quorus": entry}, "unrelated": [1, 2]})
    path.write_text(original)
    result = register_one("claude", env)
    assert result.status == "kept" and "--force" in result.detail
    assert path.read_text() == original and code_cli == []
    assert not list(home.glob("*.bak-quorus-*"))
    assert register_one("claude", env, force=True).ok
    assert len(list(home.glob("*.bak-quorus-*"))) == 1
    data = json.loads(path.read_text())
    assert data["unrelated"] == [1, 2] and data["liveCounter"] == 2
    assert data["mcpServers"]["quorus"]["env"] == env.env_block("claude")


def test_manual_command_instead_of_writing_settings(home, env):
    (home / ".claude").mkdir()
    settings = home / ".claude" / "settings.json"
    settings.write_text('{"permissions":{"allow":["Read(*)"]}}')
    result = claude.register_claude_code(env)
    assert result.status == "manual" and "mcp add-json quorus" in result.detail
    assert "-s user" in result.detail and "synthetic-key" not in result.detail
    assert json.loads(settings.read_text()) == {"permissions": {"allow": ["Read(*)"]}}
    assert not (home / ".claude.json").exists()


def test_custom_code_config_directory_guard(home, env, monkeypatch):
    root = home / "custom-claude"
    root.mkdir()
    path = root / ".claude.json"
    original = json.dumps({"mcpServers": {"quorus": {"env": {
        "QUORUS_INSTANCE_NAME": "old", "QUORUS_RELAY_URL": "https://old.test"}}}})
    path.write_text(original)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    result = register_one("claude", env)
    assert result.status == "kept" and result.path == path
    assert path.read_text() == original
    assert not (home / ".claude.json").exists()


def test_register_all_summary_keeps_identity_without_project_fallback(home, env):
    from quorus_cli import cli

    path = claude.desktop_config_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"quorus": {"env": {
        "QUORUS_INSTANCE_NAME": "old-desktop", "QUORUS_RELAY_URL": "https://old.test"}}}}))
    results = register_all(env)
    assert next(r for r in results if r.platform == "Claude Desktop").status == "kept"
    ui = MagicMock()
    cli._register_mcp_everywhere(name="new", relay_url=env.relay_url, api_key="",
                                 mcp_command=env.command, mcp_args=[], ui=ui)
    printed = " ".join(str(call.args[0]) for call in ui.console.print.call_args_list)
    assert "kept existing @old-desktop on https://old.test" in printed
    assert not (home / ".mcp.json").exists()
    assert not (home / ".claude.json").exists()


def test_json_write_is_backed_up_before_atomic_replace(home, env, monkeypatch):
    path = home / ".cursor" / "mcp.json"
    path.parent.mkdir()
    before = '{"other":{"keep":true}}'
    path.write_text(before)
    real_replace = config_io.os.replace
    def replace(source, target):
        assert path.read_text() == before
        backups = list(path.parent.glob("mcp.json.bak-quorus-*"))
        assert len(backups) == 1 and backups[0].read_text() == before
        real_replace(source, target)
    monkeypatch.setattr(config_io.os, "replace", replace)
    assert register_cursor(env).ok
    assert json.loads(path.read_text())["other"] == {"keep": True}


@pytest.mark.parametrize("platform", ["claude", "claude-desktop", "cursor", "codex",
                                     "gemini", "windsurf", "opencode", "continue", "cline"])
def test_every_writer_keeps_another_identity_until_force(home, env, code_cli, platform):
    old = McpEnv(command=env.command, args=[], relay_url="https://old.test",
                 instance_name_base="old")
    first = register_one(platform, old)
    assert first.ok
    before = first.path.read_bytes()
    result = register_one(platform, env)
    assert result.status == "kept" and "--force" in result.detail
    assert first.path.read_bytes() == before
    assert register_one(platform, env, force=True).ok
    backups = list(first.path.parent.glob(first.path.name + ".bak-quorus-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before


def test_atomic_failure_keeps_original_and_backup(home, env, monkeypatch):
    path = home / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text('{"other":true}')
    def fail(*args):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(config_io.os, "replace", fail)
    assert register_cursor(env).status == "error"
    assert path.read_text() == '{"other":true}'
    assert len(list(path.parent.glob("*.bak-quorus-*"))) == 1
    assert not list(path.parent.glob(".quorus-*"))


def test_code_failure_restores_only_its_entry(home, env, code_cli, monkeypatch):
    path = home / ".claude.json"
    old = {"command": "old", "env": {"QUORUS_INSTANCE_NAME": "old",
                                       "QUORUS_RELAY_URL": "https://old.test"}}
    path.write_text(json.dumps({"unrelated": 4, "mcpServers": {"quorus": old}}))
    real_run = claude.subprocess.run
    def fail_new(args, **kwargs):
        if args[2] == "add-json" and json.loads(args[4])["command"] == env.command:
            raise subprocess.CalledProcessError(1, args)
        return real_run(args, **kwargs)
    monkeypatch.setattr(claude.subprocess, "run", fail_new)
    assert register_one("claude", env, force=True).status == "error"
    data = json.loads(path.read_text())
    assert data["mcpServers"]["quorus"] == old and data["unrelated"] == 4
    assert data["liveCounter"] == 2


@pytest.mark.parametrize("force", [False, True])
def test_connect_parser_exposes_force_for_desktop(home, monkeypatch, force):
    from quorus_cli import cli

    captured = []
    monkeypatch.setattr(cli, "_cmd_connect", lambda args: captured.append(args))
    argv = ["quorus", "connect", "claude-desktop", "room", "name"]
    monkeypatch.setattr("sys.argv", argv + (["--force"] if force else []))
    cli.main()
    assert captured[0].force is force
