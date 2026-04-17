from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from quorus_cli.codex_agent import (
    CodexAgentError,
    build_codex_command,
    build_prompt,
    resolve_identity,
)


def test_resolve_identity_returns_parent_when_no_override() -> None:
    name, api_key = resolve_identity(
        relay_url="https://relay.test",
        parent_name="arav-codex",
        parent_api_key="mct_parent",
        requested_name=None,
        suffix=None,
    )

    assert name == "arav-codex"
    assert api_key == "mct_parent"


def test_resolve_identity_registers_child_from_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.json.return_value = {
        "agent_name": "arav-codex-reviewer",
        "api_key": "mct_child",
    }
    response.raise_for_status.return_value = None

    def fake_post(url: str, **kwargs):
        assert url == "https://relay.test/v1/auth/register-agent"
        assert kwargs["json"] == {"suffix": "reviewer"}
        assert kwargs["headers"]["Authorization"] == "Bearer mct_parent"
        return response

    monkeypatch.setattr("quorus_cli.codex_agent.httpx.post", fake_post)

    name, api_key = resolve_identity(
        relay_url="https://relay.test",
        parent_name="arav-codex",
        parent_api_key="mct_parent",
        requested_name=None,
        suffix="reviewer",
    )

    assert name == "arav-codex-reviewer"
    assert api_key == "mct_child"


def test_resolve_identity_rejects_non_child_name() -> None:
    with pytest.raises(CodexAgentError):
        resolve_identity(
            relay_url="https://relay.test",
            parent_name="arav-codex",
            parent_api_key="mct_parent",
            requested_name="somebody-else",
            suffix=None,
        )


def test_build_prompt_mentions_room_and_inbox() -> None:
    prompt = build_prompt(
        "medbuddy-sprint",
        "arav-codex-reviewer",
        Path("/tmp/quorus-arav-codex-reviewer-inbox.txt"),
    )

    assert "medbuddy-sprint" in prompt
    assert "arav-codex-reviewer" in prompt
    assert "/tmp/quorus-arav-codex-reviewer-inbox.txt" in prompt


def test_build_codex_command_includes_quorus_overrides() -> None:
    cmd = build_codex_command(
        room="medbuddy-sprint",
        participant="arav-codex-reviewer",
        relay_url="https://relay.test",
        api_key="mct_child",
        cwd=Path("/tmp/workspace"),
        sandbox="workspace-write",
        approval="on-request",
        inbox_path=Path("/tmp/quorus-arav-codex-reviewer-inbox.txt"),
    )

    assert cmd[:7] == [
        "codex",
        "-C",
        "/tmp/workspace",
        "-s",
        "workspace-write",
        "-a",
        "on-request",
    ]
    assert 'mcp_servers.quorus.env.QUORUS_INSTANCE_NAME="arav-codex-reviewer"' in cmd
    assert 'mcp_servers.quorus.env.QUORUS_API_KEY="mct_child"' in cmd
    assert 'mcp_servers.quorus.env.QUORUS_RELAY_URL="https://relay.test"' in cmd
    assert "medbuddy-sprint" in cmd[-1]
