""""default" can't be your name: `quorus agent add` reads it as no name set.

Codex's first-run audit: the wizard accepted `default`, then agent_cmd._add
refused it ("set your name first"), so that user could never add an agent.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from quorus_tui import hub
from rich.console import Console


def test_wizard_rejects_default_then_takes_the_next_name(monkeypatch):
    answers = iter(["default", "Default", "alice"])
    monkeypatch.setattr(hub.Prompt, "ask", lambda *a, **kw: next(answers))
    console = Console(record=True, width=100)
    assert hub._prompt_name(console) == "alice"
    assert console.export_text().count("reserved") == 2


@pytest.mark.parametrize("name", ["default", "DEFAULT"])
def test_init_rejects_default(name, tmp_path, monkeypatch):
    from quorus_cli.cli import _cmd_init

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    args = MagicMock()
    args.name, args.relay_url, args.secret, args.api_key = name, "http://127.0.0.1:1", "s", None
    with pytest.raises(SystemExit):
        _cmd_init(args)
    assert not any(tmp_path.iterdir())  # nothing saved
