"""First-run hint wording (weekend brief, findings #5 and #8).

- #8 `quorus ps` on an empty room suggested `quorus spawn <room> <name>`
  without saying that it launches a vendor CLI in this terminal — a new user
  can't tell it apart from registering a background agent.
- #5 the flat-config branch of `quorus init` said only "overwriting", without
  naming what it replaces (the relay URL and credentials).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import quorus_cli.cli as cli


def _mock_client(json_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    return client


def _flat(capsys) -> str:
    return " ".join(capsys.readouterr().out.split())


@pytest.mark.asyncio
async def test_ps_empty_state_says_what_spawn_does(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_get_client", lambda: _mock_client([]))

    await cli._ps()

    out = _flat(capsys)
    assert "no agents reporting presence yet" in out
    assert "quorus agent add <tool> --room <room>" in out
    assert "quorus spawn <room> <name>" in out
    assert "drive a CLI in this terminal" in out


def test_grouped_help_names_every_harness_agent_add_supports(capsys) -> None:
    """Release check: `quorus --help` listed Claude/Codex/Gemini but not opencode."""
    from quorus_cli.cli import _print_grouped_help

    _print_grouped_help()
    line = next(x for x in capsys.readouterr().out.splitlines() if "agent add <tool>" in x)
    for tool in ("Claude", "Codex", "Gemini", "opencode"):
        assert tool in line, line
