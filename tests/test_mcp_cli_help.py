"""Console help must not start stdio transport or contact the relay."""

import sys

import pytest
from quorus_mcp import server


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_mcp_help_does_not_start_transport(monkeypatch, capsys, flag):
    starts = []
    monkeypatch.setattr(sys, "argv", ["quorus-mcp", flag])
    monkeypatch.setattr(server.mcp, "run", lambda **kw: starts.append(kw))
    server.main_cli()
    assert starts == []
    assert "quorus-mcp" in capsys.readouterr().out
