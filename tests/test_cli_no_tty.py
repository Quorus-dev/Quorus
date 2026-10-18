"""Bare ``quorus`` without a terminal (0.5.0 published-package sweep, A1).

An agent or a CI script that runs plain ``quorus`` used to get the interactive
hub, which prompted into a closed stdin and exited 0 having done nothing. It
must instead print the non-interactive setup on stderr and exit 2.
"""

from __future__ import annotations

import io

import pytest
import quorus_cli.cli as cli


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _run_bare(monkeypatch: pytest.MonkeyPatch, *, stdin_tty: bool, stdout_tty: bool):
    monkeypatch.setattr(cli.sys, "argv", ["quorus"])
    monkeypatch.setattr(cli.sys, "stdin", _Stream(stdin_tty))
    monkeypatch.setattr(cli.sys, "stdout", _Stream(stdout_tty))
    err = _Stream(False)
    monkeypatch.setattr(cli.sys, "stderr", err)
    opened: list[object] = []
    monkeypatch.setattr(cli, "_cmd_begin", lambda args: opened.append(args))
    return err, opened


def test_bare_quorus_without_tty_prints_setup_and_exits_2(monkeypatch) -> None:
    err, opened = _run_bare(monkeypatch, stdin_tty=False, stdout_tty=False)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert opened == [], "the hub must not open without a terminal"
    text = err.getvalue()
    assert "quorus init <name> --relay-url http://localhost:8080 --secret -" in text
    assert "--api-key -" in text and "stdin" in text
    assert "quorus rooms --json" in text
    assert "quorus-mcp" in text and "llms.txt" in text


def test_bare_quorus_with_piped_stdout_still_refuses(monkeypatch) -> None:
    """`quorus | tee log` has a keyboard but nowhere to draw: the hub is useless."""
    _, opened = _run_bare(monkeypatch, stdin_tty=True, stdout_tty=False)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert opened == []


def test_bare_quorus_with_tty_opens_the_hub(monkeypatch) -> None:
    err, opened = _run_bare(monkeypatch, stdin_tty=True, stdout_tty=True)
    cli.main()
    assert len(opened) == 1
    assert err.getvalue() == ""
