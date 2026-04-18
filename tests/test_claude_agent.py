"""Unit tests for packages/cli/quorus_cli/claude_agent.py.

Tests the Claude Code autonomous runner infrastructure:
- Identity resolution and caching
- Room context formatting
- Command building (interactive and print mode)
- Autonomous prompt generation
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from quorus_cli.claude_agent import (
    ClaudeAgentError,
    _build_mcp_config,
    _format_room_context,
    _autonomous_prompt,
    build_claude_command,
    build_claude_print_command,
    build_prompt,
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
        assert cmd[-1] == "Execute this task now"


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
