"""Fallbacks never spend a signed-in account without explicit consent."""
import io
from types import SimpleNamespace

import pytest
from quorus_cli import demo_cmd, quickstart
from rich.console import Console


@pytest.mark.parametrize("tty,opt_in,answer,expected", [
    (False, False, None, False), (False, True, None, True),
    (True, False, False, False), (True, False, True, True),
    (True, True, None, True),
])
def test_fallback_requires_disclosed_consent(monkeypatch, tty, opt_in, answer, expected):
    from rich.prompt import Confirm

    monkeypatch.setattr(demo_cmd, "fallback_cast", lambda: [("codex", None)] * 2)
    monkeypatch.setattr(demo_cmd.sys, "stdin", SimpleNamespace(isatty=lambda: tty))
    out = io.StringIO()
    console = Console(file=out, width=160)

    def ask(*a, **kw):
        assert tty and not opt_in
        assert kw["default"] is False
        assert "your account's usage" in out.getvalue()
        return answer

    monkeypatch.setattr(Confirm, "ask", ask)
    result = demo_cmd.consented_fallback(SimpleNamespace(use_my_login=opt_in), console)
    assert bool(result) is expected
    assert "signed-in Codex" in out.getvalue()
    assert "your account's usage" in out.getvalue()


def test_canceled_fallback_is_not_consent(monkeypatch):
    from rich.prompt import Confirm

    monkeypatch.setattr(demo_cmd, "fallback_cast", lambda: [("claude", None)] * 2)
    monkeypatch.setattr(demo_cmd.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def cancel(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(Confirm, "ask", cancel)
    assert demo_cmd.consented_fallback(SimpleNamespace(), Console(file=io.StringIO())) == []


@pytest.mark.parametrize("problem", [None, "opencode is not installed"])
def test_script_without_opt_in_starts_nothing_and_cleans_up(monkeypatch, tmp_path, problem):
    root = tmp_path / "demo"
    root.mkdir()
    monkeypatch.setattr(demo_cmd.tempfile, "mkdtemp", lambda **k: str(root))
    monkeypatch.setattr(demo_cmd, "_opencode_ok", lambda: problem)
    monkeypatch.setattr(demo_cmd, "_free_port", lambda: pytest.fail("allocated a port"))
    monkeypatch.setattr(demo_cmd, "make_sample_repo", lambda r: r)
    monkeypatch.setattr(demo_cmd, "write_binding", lambda *a: None)
    monkeypatch.setattr(demo_cmd, "pick_models", lambda *a: [])
    monkeypatch.setattr(demo_cmd, "fallback_cast", lambda: [("codex", None)] * 2)
    monkeypatch.setattr(demo_cmd.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(demo_cmd, "share_login", lambda *a: pytest.fail("shared login"))
    # The all-busy exit prints the next command for *this* machine; detecting it runs
    # `claude auth status` where Claude Code is installed, which the Popen guard below
    # would read as an agent starting. Pin the machine so the test doesn't depend on PATH.
    monkeypatch.setattr(quickstart, "machine_state", lambda **k: quickstart.MachineState(
        ollama="absent", ollama_model=None, claude=False, codex=False, gemini_env=False,
        opencode=False, gemini_cli=False, configured=False))
    monkeypatch.setattr(demo_cmd.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("process started"))
    out = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        demo_cmd.cmd_demo(SimpleNamespace(model=None, keep=False), Console(file=out))
    if problem:
        assert "opencode isn't usable: opencode is not installed" in out.getvalue()
        assert "all busy" not in out.getvalue()
    else:
        assert "all busy" in out.getvalue()
    assert exc.value.code == 1
    assert not root.exists()


def test_opt_in_flag_is_exposed_by_the_real_parser(monkeypatch, capsys):
    from quorus_cli import cli

    monkeypatch.setattr(cli.sys, "argv", ["quorus", "demo", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "--use-my-login" in capsys.readouterr().out
