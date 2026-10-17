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
