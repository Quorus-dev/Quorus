"""Unit tests for packages/cli/quorus_cli/claude_agent.py.

Tests the Claude Code autonomous runner infrastructure:
- Identity resolution (no double-suffix, parent passthrough, prefix validation)
- Heartbeat endpoint correctness
- 403 join fallback logic
- Room context formatting
- Command building (interactive and print mode)
- Autonomous prompt generation
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import httpx
import pytest
from quorus_cli.claude_agent import (
    ClaudeAgentError,
    _build_mcp_config,
    _format_room_context,
    _autonomous_prompt,
    build_claude_command,
    build_claude_print_command,
    build_prompt,
    resolve_identity,
    send_heartbeat,
    join_room,
    parent_join_room,
)


class TestBuildPrompt:
    def test_includes_participant_and_room(self):
        prompt = build_prompt(
            room="test-room",
            participant="alice-claude",
            inbox_path=Path("/tmp/inbox.txt"),
        )
        assert "alice-claude" in prompt
        assert "test-room" in prompt
        assert "Quorus MCP tools" in prompt

    def test_includes_context_path_when_provided(self):
        prompt = build_prompt(
            room="test-room",
            participant="alice-claude",
            inbox_path=Path("/tmp/inbox.txt"),
            context_path=Path("/tmp/context.md"),
        )
        assert "/tmp/context.md" in prompt

    def test_includes_autonomous_instructions(self):
        prompt = build_prompt(
            room="test-room",
            participant="alice-claude",
            inbox_path=Path("/tmp/inbox.txt"),
        )
        assert "autonomously" in prompt
        assert "Post concise status updates" in prompt


class TestMcpConfig:
    def test_includes_quorus_server(self):
        config = _build_mcp_config(
            participant="alice-claude",
            relay_url="https://relay.test",
            api_key="mct_test_key",
        )
        assert "mcpServers" in config
        assert "quorus" in config["mcpServers"]
        assert config["mcpServers"]["quorus"]["env"]["QUORUS_INSTANCE_NAME"] == "alice-claude"
        assert config["mcpServers"]["quorus"]["env"]["QUORUS_RELAY_URL"] == "https://relay.test"
        assert config["mcpServers"]["quorus"]["env"]["QUORUS_API_KEY"] == "mct_test_key"

    def test_uses_relay_secret_when_no_api_key(self):
        config = _build_mcp_config(
            participant="bob-claude",
            relay_url="https://relay.test",
            relay_secret="legacy_secret",
        )
        assert "QUORUS_RELAY_SECRET" in config["mcpServers"]["quorus"]["env"]
        assert "QUORUS_API_KEY" not in config["mcpServers"]["quorus"]["env"]


class TestBuildClaudeCommand:
    def test_includes_model_and_permission_mode(self):
        cmd = build_claude_command(
            room="test-room",
            participant="alice-claude",
            relay_url="https://relay.test",
            api_key="mct_test",
            cwd=Path("/home/user/project"),
            permission_mode="auto",
            inbox_path=Path("/tmp/inbox.txt"),
            model="opus",
        )
        assert "claude" in cmd
        assert "--model" in cmd
        assert "opus" in cmd
        assert "--permission-mode" in cmd
        assert "auto" in cmd

    def test_includes_mcp_config(self):
        cmd = build_claude_command(
            room="test-room",
            participant="alice-claude",
            relay_url="https://relay.test",
            api_key="mct_test",
            cwd=Path("/home/user/project"),
            permission_mode="auto",
            inbox_path=Path("/tmp/inbox.txt"),
        )
        assert "--mcp-config" in cmd
        mcp_idx = cmd.index("--mcp-config")
        mcp_json = cmd[mcp_idx + 1]
        config = json.loads(mcp_json)
        assert "mcpServers" in config
        assert "quorus" in config["mcpServers"]


class TestBuildClaudePrintCommand:
    def test_includes_print_flag(self):
        cmd = build_claude_print_command(
            room="test-room",
            participant="alice-claude",
            relay_url="https://relay.test",
            api_key="mct_test",
            cwd=Path("/home/user/project"),
            permission_mode="auto",
            inbox_path=Path("/tmp/inbox.txt"),
            context_path=Path("/tmp/context.md"),
            prompt="Do the task",
        )
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "text" in cmd

    def test_prompt_is_last_argument(self):
        cmd = build_claude_print_command(
            room="test-room",
            participant="alice-claude",
            relay_url="https://relay.test",
            api_key="mct_test",
            cwd=Path("/home/user/project"),
            permission_mode="auto",
            inbox_path=Path("/tmp/inbox.txt"),
            context_path=Path("/tmp/context.md"),
            prompt="Execute this task now",
        )
        # Last arg is the combined system+user prompt; user prompt must be embedded.
        assert "Execute this task now" in cmd[-1]
        assert "alice-claude" in cmd[-1]


class TestAutonomousPrompt:
    def test_initial_scan_prompt(self):
        prompt = _autonomous_prompt(
            room="test-room",
            participant="alice-claude",
            context_path=Path("/tmp/context.md"),
            new_messages=[],
            initial_scan=True,
        )
        assert "Review the current Quorus room" in prompt
        assert "test-room" in prompt
        assert "/tmp/context.md" in prompt

    def test_new_messages_prompt(self):
        prompt = _autonomous_prompt(
            room="test-room",
            participant="alice-claude",
            context_path=Path("/tmp/context.md"),
            new_messages=[
                {"from_name": "bob", "content": "Need help with the API"},
                {"from_name": "carol", "content": "I can take the frontend"},
            ],
            initial_scan=False,
        )
        assert "New Quorus room activity" in prompt
        assert "bob: Need help with the API" in prompt
        assert "carol: I can take the frontend" in prompt


class TestFormatRoomContext:
    def test_includes_room_and_participant(self):
        context = _format_room_context(
            room="test-room",
            participant="alice-claude",
            state={},
            history=[],
        )
        assert "# Quorus Room: test-room" in context
        assert "Participant: alice-claude" in context

    def test_includes_active_agents(self):
        context = _format_room_context(
            room="test-room",
            participant="alice-claude",
            state={"active_agents": ["bob-codex", "carol-gemini"]},
            history=[],
        )
        assert "## Active Agents" in context
        assert "bob-codex" in context
        assert "carol-gemini" in context

    def test_includes_recent_messages(self):
        context = _format_room_context(
            room="test-room",
            participant="alice-claude",
            state={},
            history=[
                {
                    "from_name": "bob",
                    "message_type": "chat",
                    "timestamp": "2024-01-01T12:00:00",
                    "content": "Hello team",
                },
            ],
        )
        assert "## Recent Room Messages" in context
        assert "bob [chat]: Hello team" in context

    def test_includes_locked_files(self):
        context = _format_room_context(
            room="test-room",
            participant="alice-claude",
            state={
                "locked_files": {
                    "src/main.py": {"held_by": "bob-codex"},
                }
            },
            history=[],
        )
        assert "## Locked Files" in context
        assert "src/main.py: bob-codex" in context

    def test_includes_active_goal(self):
        context = _format_room_context(
            room="test-room",
            participant="alice-claude",
            state={"active_goal": "Build the MVP by EOD"},
            history=[],
        )
        assert "## Active Goal" in context
        assert "Build the MVP by EOD" in context


# ---------------------------------------------------------------------------
# resolve_identity — the no-double-suffix invariant
# ---------------------------------------------------------------------------

class TestResolveIdentity:
    RELAY = "https://relay.test"
    PARENT = "arav"
    PARENT_KEY = "mct_parent_key"

    def test_no_name_no_suffix_returns_parent(self):
        """Core invariant: no --name + no --suffix → run as parent, no child created."""
        name, key = resolve_identity(
            relay_url=self.RELAY,
            parent_name=self.PARENT,
            parent_api_key=self.PARENT_KEY,
            requested_name=None,
            suffix=None,
        )
        assert name == self.PARENT
        assert key == self.PARENT_KEY

    def test_requested_name_equals_parent_returns_parent(self):
        """--name arav when parent is arav → run as parent directly."""
        name, key = resolve_identity(
            relay_url=self.RELAY,
            parent_name=self.PARENT,
            parent_api_key=self.PARENT_KEY,
            requested_name=self.PARENT,
            suffix=None,
        )
        assert name == self.PARENT
        assert key == self.PARENT_KEY

    def test_both_name_and_suffix_raises(self):
        with pytest.raises(ClaudeAgentError, match="either --name or --suffix"):
            resolve_identity(
                relay_url=self.RELAY,
                parent_name=self.PARENT,
                parent_api_key=self.PARENT_KEY,
                requested_name="arav-claude",
                suffix="claude",
            )

    def test_name_without_parent_prefix_raises(self):
        """--name aarya-claude when parent is arav → reject (not this user's child)."""
        with pytest.raises(ClaudeAgentError, match="must equal"):
            resolve_identity(
                relay_url=self.RELAY,
                parent_name=self.PARENT,
                parent_api_key=self.PARENT_KEY,
                requested_name="aarya-claude",
                suffix=None,
            )

    def test_suffix_registers_child_via_relay(self):
        """--suffix claude → registers arav-claude child and returns its key."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "agent_name": "arav-claude",
            "api_key": "mct_child_key",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("quorus_cli.claude_agent._cached_child_api_key", return_value=""), \
             patch("quorus_cli.claude_agent._save_child_api_key") as mock_save, \
             patch("httpx.post", return_value=mock_resp):
            name, key = resolve_identity(
                relay_url=self.RELAY,
                parent_name=self.PARENT,
                parent_api_key=self.PARENT_KEY,
                requested_name=None,
                suffix="claude",
            )

        assert name == "arav-claude"
        assert key == "mct_child_key"
        mock_save.assert_called_once_with("arav-claude", "mct_child_key")

    def test_cached_child_key_skips_registration(self):
        """Cached child API key → no relay call needed."""
        with patch("quorus_cli.claude_agent._cached_child_api_key", return_value="mct_cached"), \
             patch("httpx.post") as mock_post:
            name, key = resolve_identity(
                relay_url=self.RELAY,
                parent_name=self.PARENT,
                parent_api_key=self.PARENT_KEY,
                requested_name=None,
                suffix="claude",
            )

        assert name == "arav-claude"
        assert key == "mct_cached"
        mock_post.assert_not_called()

    def test_name_with_parent_prefix_accepted(self):
        """--name arav-cursor with parent arav → suffix=cursor, registers child."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "agent_name": "arav-cursor",
            "api_key": "mct_cursor_key",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("quorus_cli.claude_agent._cached_child_api_key", return_value=""), \
             patch("quorus_cli.claude_agent._save_child_api_key"), \
             patch("httpx.post", return_value=mock_resp):
            name, key = resolve_identity(
                relay_url=self.RELAY,
                parent_name=self.PARENT,
                parent_api_key=self.PARENT_KEY,
                requested_name="arav-cursor",
                suffix=None,
            )

        assert name == "arav-cursor"
        assert key == "mct_cursor_key"


# ---------------------------------------------------------------------------
# send_heartbeat — correct endpoint + payload
# ---------------------------------------------------------------------------

class TestSendHeartbeat:
    def test_posts_to_top_level_heartbeat_endpoint(self):
        """Must use /heartbeat, NOT /rooms/{room}/heartbeat (old wrong path)."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_token_resp = MagicMock()
        mock_token_resp.raise_for_status = MagicMock()
        mock_token_resp.json.return_value = {"token": "jwt_tok"}

        captured_url = []

        def fake_request(method, url, **kwargs):
            captured_url.append(url)
            return mock_resp

        with patch("httpx.post", return_value=mock_token_resp), \
             patch("httpx.request", side_effect=fake_request):
            send_heartbeat(
                relay_url="https://relay.test",
                room="medbuddy-sprint",
                participant="arav-claude",
                api_key="mct_test",
            )

        assert any("heartbeat" in u for u in captured_url), f"No heartbeat URL found in {captured_url}"
        assert not any("/rooms/" in u for u in captured_url), \
            f"Used wrong room-scoped heartbeat endpoint: {captured_url}"

    def test_heartbeat_payload_includes_required_fields(self):
        """Payload must include instance_name, status=active, room."""
        captured_body = {}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_token_resp = MagicMock()
        mock_token_resp.raise_for_status = MagicMock()
        mock_token_resp.json.return_value = {"token": "jwt_tok"}

        def fake_request(method, url, json=None, **kwargs):
            if json:
                captured_body.update(json)
            return mock_resp

        with patch("httpx.post", return_value=mock_token_resp), \
             patch("httpx.request", side_effect=fake_request):
            send_heartbeat(
                relay_url="https://relay.test",
                room="medbuddy-sprint",
                participant="arav-claude",
                api_key="mct_test",
            )

        assert captured_body.get("instance_name") == "arav-claude"
        assert captured_body.get("status") == "active"
        assert captured_body.get("room") == "medbuddy-sprint"


# ---------------------------------------------------------------------------
# 403 join fallback — parent-assisted join
# ---------------------------------------------------------------------------

class TestJoinRoomFallback:
    def test_join_room_posts_to_correct_url(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_token_resp = MagicMock()
        mock_token_resp.raise_for_status = MagicMock()
        mock_token_resp.json.return_value = {"token": "tok"}

        captured = []

        def fake_request(method, url, **kwargs):
            captured.append((method, url))
            return mock_resp

        with patch("httpx.post", return_value=mock_token_resp), \
             patch("httpx.request", side_effect=fake_request):
            join_room(
                relay_url="https://relay.test",
                room="general",
                participant="arav-claude",
                api_key="mct_test",
            )

        assert any("rooms/general/join" in u for _, u in captured)

    def test_parent_join_room_uses_parent_api_key(self):
        """parent_join_room must authenticate as parent, not child."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_token_resp = MagicMock()
        mock_token_resp.raise_for_status = MagicMock()
        mock_token_resp.json.return_value = {"token": "parent_tok"}

        captured_auth = []

        def fake_post(url, **kwargs):
            # Token exchange captures the api_key used
            body = kwargs.get("json", {})
            if "api_key" in body:
                captured_auth.append(body["api_key"])
            return mock_token_resp

        def fake_request(method, url, **kwargs):
            return mock_resp

        with patch("httpx.post", side_effect=fake_post), \
             patch("httpx.request", side_effect=fake_request):
            parent_join_room(
                relay_url="https://relay.test",
                room="general",
                participant="arav-claude",
                parent_api_key="mct_parent_key",
            )

        assert "mct_parent_key" in captured_auth, \
            "parent_join_room must authenticate with parent API key"
