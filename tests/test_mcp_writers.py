"""Unit tests for packages/cli/quorus_cli/mcp_writers.py.

Each writer gets two kinds of coverage:

* ``not_installed`` behavior when the target config file doesn't exist
* ``wrote`` vs ``updated`` behavior with ``force=True`` or an existing
  config, including preservation of other entries the user had set

The Codex writer gets extra TOML-specific tests since it's the one
non-JSON path and we hand-rolled the serializer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from quorus_cli.mcp_writers import (
    McpEnv,
    WriteResult,
    _render_codex_toml,
    _repair_codex_toml,
    register_all,
    register_claude_code,
    register_claude_desktop,
    register_codex,
    register_continue,
    register_cursor,
    register_gemini_cli,
    register_one,
    register_opencode,
    register_windsurf,
)


@pytest.fixture
def env() -> McpEnv:
    return McpEnv(
        command="uv",
        args=["run", "python", "-m", "quorus.mcp_server"],
        relay_url="https://quorus-relay.fly.dev",
        api_key="mct_abc_xyz",
        instance_name_base="arav",
    )


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() to a scratch dir so writers are sandboxed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() reads HOME on macOS+Linux, so setting it is enough.
    # Also mock shutil.which to prevent detection of system-installed binaries
    # like `codex` that would bypass the HOME-based detection.
    monkeypatch.setattr("shutil.which", lambda _: None)
    return tmp_path


# ---------------------------------------------------------------------------
# Identity + env block
# ---------------------------------------------------------------------------


class TestMcpEnv:
    def test_agent_identity_is_name_plus_platform(self, env: McpEnv):
        assert env.agent_identity("claude") == "arav-claude"
        assert env.agent_identity("codex") == "arav-codex"

    def test_agent_identity_avoids_double_platform_suffix(self):
        env = McpEnv(
            command="uv",
            args=[],
            relay_url="http://r",
            instance_name_base="arav-codex",
        )
        assert env.agent_identity("codex") == "arav-codex"

    def test_agent_identity_falls_back_to_platform_when_unnamed(self):
        env = McpEnv(command="uv", args=[], relay_url="http://r")
        assert env.agent_identity("claude") == "claude"

    def test_env_block_prefers_api_key_over_secret(self, env: McpEnv):
        env.api_key = "mct_a"
        env.relay_secret = "legacy"
        block = env.env_block("claude")
        assert block["QUORUS_API_KEY"] == "mct_a"
        assert "QUORUS_RELAY_SECRET" not in block

    def test_env_block_falls_back_to_secret_when_no_key(self):
        env = McpEnv(
            command="uv", args=[], relay_url="http://r",
            relay_secret="s3cret",
            instance_name_base="bob",
        )
        block = env.env_block("cursor")
        assert block["QUORUS_RELAY_SECRET"] == "s3cret"
        assert block["QUORUS_INSTANCE_NAME"] == "bob-cursor"


# ---------------------------------------------------------------------------
# Standard JSON writers — auto-detect
# ---------------------------------------------------------------------------


class TestAutoDetect:
    def test_not_installed_when_file_missing(
        self, fake_home: Path, env: McpEnv
    ):
        # No `~/.claude/settings.json` exists → skip, don't write.
        r = register_claude_code(env)
        assert r.status == "not_installed"
        assert not (fake_home / ".claude" / "settings.json").exists()

    def test_writes_when_file_exists(
        self, fake_home: Path, env: McpEnv
    ):
        p = fake_home / ".claude" / "settings.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"other": "keep-me"}))
        r = register_claude_code(env)
        assert r.status == "wrote"
        data = json.loads(p.read_text())
        assert data["other"] == "keep-me"  # preserved
        assert data["mcpServers"]["quorus"]["command"] == "uv"
        assert (
            data["mcpServers"]["quorus"]["env"]["QUORUS_INSTANCE_NAME"]
            == "arav-claude"
        )

    def test_updated_when_quorus_already_present(
        self, fake_home: Path, env: McpEnv
    ):
        p = fake_home / ".claude" / "settings.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({
            "mcpServers": {"quorus": {"stale": True}, "other": {}},
        }))
        r = register_claude_code(env)
        assert r.status == "updated"
        data = json.loads(p.read_text())
        # Our write replaced the quorus entry but kept the other one.
        assert "stale" not in data["mcpServers"]["quorus"]
        assert data["mcpServers"]["other"] == {}

    def test_force_creates_missing_file(
        self, fake_home: Path, env: McpEnv
    ):
        r = register_cursor(env, force=True)
        assert r.status == "wrote"
        p = fake_home / ".cursor" / "mcp.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert (
            data["mcpServers"]["quorus"]["env"]["QUORUS_INSTANCE_NAME"]
            == "arav-cursor"
        )

    def test_error_on_malformed_json_doesnt_nuke(
        self, fake_home: Path, env: McpEnv
    ):
        p = fake_home / ".gemini" / "settings.json"
        p.parent.mkdir(parents=True)
        p.write_text("{bad json")
        r = register_gemini_cli(env)
        assert r.status == "error"
        # Original content preserved — we did not write anything.
        assert p.read_text() == "{bad json"

    @pytest.mark.parametrize("writer,path_suffix,platform_key", [
        (register_claude_desktop, ".claude.json", "claude-desktop"),
        (register_cursor, ".cursor/mcp.json", "cursor"),
        (register_windsurf, ".codeium/windsurf/mcp_config.json", "windsurf"),
        (register_gemini_cli, ".gemini/settings.json", "gemini"),
    ])
    def test_each_writer_sets_its_identity(
        self, fake_home: Path, env: McpEnv, writer, path_suffix, platform_key,
    ):
        p = fake_home / path_suffix
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
        r = writer(env)
        assert r.ok
        data = json.loads(p.read_text())
        assert (
            data["mcpServers"]["quorus"]["env"]["QUORUS_INSTANCE_NAME"]
            == f"arav-{platform_key}"
        )


# ---------------------------------------------------------------------------
# Continue (nested experimental.modelContextProtocolServers list)
# ---------------------------------------------------------------------------


class TestContinue:
    def test_appends_to_empty_list(self, fake_home: Path, env: McpEnv):
        p = fake_home / ".continue" / "config.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"models": ["gpt-4"]}))
        r = register_continue(env)
        assert r.status == "wrote"
        data = json.loads(p.read_text())
        assert data["models"] == ["gpt-4"]  # preserved
        servers = data["experimental"]["modelContextProtocolServers"]
        assert any(s["name"] == "quorus" for s in servers)

    def test_replaces_existing_quorus_entry(self, fake_home: Path, env: McpEnv):
        p = fake_home / ".continue" / "config.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({
            "experimental": {
                "modelContextProtocolServers": [
                    {"name": "other", "transport": {}},
                    {"name": "quorus", "transport": {"stale": True}},
                ]
            }
        }))
        r = register_continue(env)
        assert r.status == "updated"
        data = json.loads(p.read_text())
        servers = data["experimental"]["modelContextProtocolServers"]
        # Still 2 entries, quorus's transport was replaced.
        assert len(servers) == 2
        quorus = next(s for s in servers if s["name"] == "quorus")
        assert "stale" not in quorus["transport"]


# ---------------------------------------------------------------------------
# Opencode (nested mcp.<name> schema)
# ---------------------------------------------------------------------------


class TestOpencode:
    def test_writes_to_xdg_path_when_present(
        self, fake_home: Path, env: McpEnv,
    ):
        xdg = fake_home / ".config" / "opencode" / "opencode.json"
        xdg.parent.mkdir(parents=True)
        xdg.write_text("{}")
        r = register_opencode(env)
        assert r.status == "wrote"
        data = json.loads(xdg.read_text())
        assert data["mcp"]["quorus"]["enabled"] is True
        assert (
            data["mcp"]["quorus"]["env"]["QUORUS_INSTANCE_NAME"]
            == "arav-opencode"
        )

    def test_falls_back_to_home_path(self, fake_home: Path, env: McpEnv):
        home_fallback = fake_home / ".opencode.json"
        home_fallback.write_text(json.dumps({
            "mcp": {"other": {"enabled": True}},
        }))
        r = register_opencode(env)
        assert r.status == "wrote"
        data = json.loads(home_fallback.read_text())
        # Other entry preserved, quorus added.
        assert data["mcp"]["other"]["enabled"] is True
        assert "quorus" in data["mcp"]


# ---------------------------------------------------------------------------
# Codex (TOML)
# ---------------------------------------------------------------------------


class TestCodex:
    def test_force_creates_toml_from_scratch(
        self, fake_home: Path, env: McpEnv,
    ):
        r = register_codex(env, force=True)
        assert r.status == "wrote"
        p = fake_home / ".codex" / "config.toml"
        body = p.read_text()
        assert "[mcp_servers.quorus]" in body
        assert 'command = "uv"' in body
        assert "[mcp_servers.quorus.env]" in body
        assert 'QUORUS_INSTANCE_NAME = "arav-codex"' in body
        assert 'QUORUS_RELAY_URL = "https://quorus-relay.fly.dev"' in body

    def test_preserves_existing_servers(
        self, fake_home: Path, env: McpEnv,
    ):
        p = fake_home / ".codex" / "config.toml"
        p.parent.mkdir(parents=True)
        p.write_text(
            'model = "gpt-5"\n\n'
            '[mcp_servers.other]\n'
            'command = "node"\n'
            'args = ["other.js"]\n',
        )
        r = register_codex(env)
        assert r.status == "wrote"
        body = p.read_text()
        # Top-level scalar preserved.
        assert 'model = "gpt-5"' in body
        # Other server preserved.
        assert "[mcp_servers.other]" in body
        assert 'command = "node"' in body
        # Our server added.
        assert "[mcp_servers.quorus]" in body

    def test_render_codex_toml_roundtrip(self):
        data = {
            "model": "gpt-5",
            "mcp_servers": {
                "quorus": {
                    "command": "uv",
                    "args": ["run", "python"],
                    "env": {"QUORUS_INSTANCE_NAME": "arav-codex"},
                },
            },
        }
        body = _render_codex_toml(data)
        assert 'model = "gpt-5"' in body
        assert "[mcp_servers.quorus]" in body
        assert "[mcp_servers.quorus.env]" in body
        assert 'args = ["run", "python"]' in body

    def test_render_codex_toml_flattens_model_migration_keys(self):
        data = {
            "notice": {
                "model_migrations": {
                    "gpt-5": {
                        "2-codex": "gpt-5.4",
                    }
                }
            }
        }

        body = _render_codex_toml(data)

        assert "[notice.model_migrations]" in body
        assert '"gpt-5.2-codex" = "gpt-5.4"' in body

    def test_repair_codex_toml_quotes_project_paths(self):
        body = (
            'model = "gpt-5.4"\n\n'
            "[projects./Users/aravkekane/Desktop]\n"
            'trust_level = "trusted"\n'
        )

        repaired = _repair_codex_toml(body)

        assert '[projects."/Users/aravkekane/Desktop"]' in repaired

    def test_register_codex_repairs_invalid_project_headers(
        self, fake_home: Path, env: McpEnv,
    ):
        p = fake_home / ".codex" / "config.toml"
        p.parent.mkdir(parents=True)
        p.write_text(
            'model = "gpt-5.4"\n\n'
            "[projects./Users/aravkekane/Desktop]\n"
            'trust_level = "trusted"\n',
        )

        r = register_codex(env)

        assert r.ok
        body = p.read_text()
        assert '[projects."/Users/aravkekane/Desktop"]' in body
        assert "[mcp_servers.quorus]" in body


# ---------------------------------------------------------------------------
# register_all + register_one
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_register_all_runs_every_writer(
        self, fake_home: Path, env: McpEnv,
    ):
        # None installed — all should return not_installed, no crashes.
        results = register_all(env)
        assert len(results) >= 8
        assert all(isinstance(r, WriteResult) for r in results)
        assert all(r.status == "not_installed" for r in results)

    def test_register_all_only_writes_installed(
        self, fake_home: Path, env: McpEnv,
    ):
        # Fake Cursor being installed; Claude Code not.
        p = fake_home / ".cursor" / "mcp.json"
        p.parent.mkdir(parents=True)
        p.write_text("{}")
        results = register_all(env)
        by_platform = {r.platform: r for r in results}
        assert by_platform["Cursor"].status == "wrote"
        assert by_platform["Claude Code"].status == "not_installed"

    def test_register_one_forces_write(self, fake_home: Path, env: McpEnv):
        r = register_one("windsurf", env)
        assert r.status == "wrote"
        assert (
            fake_home / ".codeium" / "windsurf" / "mcp_config.json"
        ).exists()

    def test_register_one_unknown_platform(self, env: McpEnv):
        r = register_one("nonexistent-platform", env)
        assert r.status == "error"
        assert "no writer" in r.detail
