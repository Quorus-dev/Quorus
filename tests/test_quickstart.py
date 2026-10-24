"""First value in under 2 minutes (2026-10-17): after setup, the agent CLIs
already installed join the starter room with one keypress, and `quorus doctor`
on a fresh install points at `quorus`, not at a secret the user doesn't have."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from quorus_cli import agent_cmd, quickstart


class _C:
    def __init__(self) -> None:
        self.out: list[str] = []

    def print(self, *a: Any, **_k: Any) -> None:
        self.out.append(" ".join(map(str, a)))


@pytest.fixture
def tools(monkeypatch):
    """claude, codex (logged in), gemini (no key) and opencode 2 are installed."""
    monkeypatch.setattr(quickstart, "_codex_logged_in", lambda: True)
    monkeypatch.setattr(quickstart, "_claude_logged_in", lambda: True)
    monkeypatch.setattr(agent_cmd, "_gemini_has_key", lambda: False)
    monkeypatch.setattr(agent_cmd, "_opencode_version", lambda: "2.0.30")
    return lambda b: f"/bin/{b}" if b in ("claude", "codex", "gemini", "opencode") else None


def test_detect_says_which_tools_can_run(tools) -> None:
    found = {f.harness: f for f in quickstart.detect(tools)}
    assert [h for h, f in found.items() if f.ready] == ["claude", "codex", "opencode"]
    assert "aistudio" in found["gemini"].note


def test_one_keypress_adds_every_ready_agent_and_claude_leads(tools, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    calls: list[Any] = []
    c = _C()
    asked: list[str] = []

    def ask_write(q: str) -> bool:
        asked.append(q)
        return True

    added = quickstart.offer(c, "general", cwd=tmp_path, ask=lambda q: True,
                             add=lambda a, _c: calls.append(a), which=tools,
                             ask_write=ask_write)
    assert added == ["claude", "codex", "opencode"]
    assert [a.lead for a in calls] == [True, False, False]
    assert {a.repo for a in calls} == {str(tmp_path.resolve())}
    # Yes to the write question: the owner's requests get full turns.
    assert {a.mode for a in calls} == {"autonomous"}
    assert len(asked) == 1 and "edit files and run commands" in asked[0]
    assert "your account's permissions" in asked[0]
    assert any(str(tmp_path.resolve()) in line and "work in" in line for line in c.out)
    assert calls[2].model == quickstart.FREE_MODEL["opencode"]  # stays in the free tier
    # tui-polish.md §3.4: tools that can't run yet share one muted line.
    also = [line for line in c.out if "Also on this computer" in line]
    assert len(also) == 1 and "Gemini CLI (needs GEMINI_API_KEY" in also[0]
    assert not any("found but" in line for line in c.out)
    assert any("Claude Code agent leads" in line for line in c.out)


def test_saying_no_adds_nothing_and_one_failure_doesnt_stop_the_rest(tools, tmp_path) -> None:
    c = _C()
    assert quickstart.offer(c, "general", cwd=tmp_path, ask=lambda q: False,
                            add=lambda a, _c: None, which=tools) == []
    assert any("Later: quorus agent add" in line for line in c.out)

    def add(a: Any, _c: Any) -> None:
        if a.tool == "codex":
            raise SystemExit("agent: boom")
    added = quickstart.offer(_C(), "general", cwd=tmp_path, ask=lambda q: True, add=add,
                             which=tools, ask_write=lambda q: False)
    assert added == ["claude", "opencode"]


def test_enter_at_the_write_question_keeps_existing_permissions(tools, tmp_path) -> None:
    """0.6.0 stranger blocker: the wizard bound the repo in default mode, where
    codex and opencode can't write, and a stranger's first fix request bounced
    between two agents. The choice is explicit and defaults to no (codex): Enter
    keeps each tool's own permissions, and the way to switch is printed."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    calls: list[Any] = []
    c = _C()
    quickstart.offer(c, "general", cwd=tmp_path, ask=lambda q: True,
                     add=lambda a, _c: calls.append(a), which=tools,
                     ask_write=lambda q: False)
    assert calls and {a.mode for a in calls} == {None}
    assert any("--mode autonomous" in line and str(tmp_path.resolve()) in line
               for line in c.out)
    # Default mode keeps each tool's own settings, which may allow edits: the wizard
    # must not promise "no changes" (codex, review of a5b0f90e).
    assert any("permissions stay as they are" in line for line in c.out)
    assert not any("don't change files" in line for line in c.out)


def test_without_a_repo_the_write_question_is_not_asked(tools, tmp_path) -> None:
    asked: list[str] = []
    calls: list[Any] = []
    quickstart.offer(_C(), "general", cwd=tmp_path, ask=lambda q: True,
                     add=lambda a, _c: calls.append(a), which=tools,
                     ask_write=lambda q: asked.append(q) or True)
    assert asked == [] and calls and {a.mode for a in calls} == {None}


def test_no_repo_means_chat_only_and_one_agent_has_no_lead(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(agent_cmd, "_opencode_version", lambda: "2.1.0")
    calls: list[Any] = []
    quickstart.offer(_C(), "general", cwd=tmp_path, ask=lambda q: True,
                     add=lambda a, _c: calls.append(a),
                     which=lambda b: "/bin/opencode" if b == "opencode" else None,
                     ask_write=lambda q: True)
    assert len(calls) == 1 and calls[0].repo is None and calls[0].lead is False


def test_doctor_on_a_fresh_install_points_at_quorus(tmp_path: Path) -> None:
    import os
    import sys

    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "TERM": "dumb", "COLUMNS": "200",
           # cwd is tmp_path, so a relative PYTHONPATH (".:packages/cli") would resolve
           # there and the child would import whatever `quorus_cli` the venv installed.
           "PYTHONPATH": os.pathsep.join(os.path.abspath(p) for p in
                                         os.environ.get("PYTHONPATH", "").split(os.pathsep)
                                         if p)}
    out = subprocess.run([sys.executable, "-m", "quorus_cli.cli", "doctor"], env=env,
                         capture_output=True, text=True, timeout=60, cwd=tmp_path)
    text = out.stdout + out.stderr
    assert "isn't set up yet" in text and "quorus demo" in text
    assert "--secret" not in text  # the old advice needed a secret nobody has yet
    # "no keys needed" over-promised: busy free models make the demo exit 1.
    flat = " ".join(text.split())  # rich wraps at the terminal width
    assert "no keys needed" not in flat and "retry later" in flat
    # Claude on 0d4ce515: a fresh install is pointed at `quorus` and the free path, not
    # at a multi-GB local model; the next-command block belongs to the demo's all-busy
    # exit (and, later, a set-up doctor where no agent answered).
    assert "To get an answer in #general now" not in flat and "ollama" not in flat


# ── the one honest next command (keyless-answer.md §3.4) ────────────────────

def _state(**kw: Any) -> quickstart.MachineState:
    """A set-up machine with opencode and gemini installed; tests override what differs."""
    return quickstart.MachineState(**{"ollama": "absent", "configured": True, "opencode": True,
                                      "gemini_cli": True, **kw})


def test_each_machine_state_gets_one_honest_command() -> None:
    lines = quickstart.next_command_lines
    add = "quorus agent add opencode --room r --name local --model ollama/"
    assert lines("r", _state(ollama="serving", ollama_model="llama3.2:3b")) == \
        [add + "llama3.2:3b"]
    assert lines("r", _state(ollama="no_model")) == [f"ollama pull qwen3:4b && {add}qwen3:4b"]
    # installed but stopped: the server starts before any pull (Codex on 70e355d8), in
    # its own terminal, since `ollama serve` never returns (Claude on 0d4ce515)
    stopped = lines("r", _state(ollama="stopped"))
    assert stopped[0] == quickstart.OLLAMA_SERVE and stopped[1].startswith("ollama pull")
    assert "own terminal" in stopped[0] and "brew services start ollama" in stopped[0]
    assert lines("r", _state(claude=True)) == ["quorus agent add claude --room r"]
    assert lines("r", _state(codex=True)) == ["quorus agent add codex --room r"]
    assert lines("r", _state(gemini_env=True)) == \
        ["quorus agent add gemini --room r --model gemini-3.5-flash-lite"]
    bare = lines("r", _state())
    assert bare[0].startswith("brew install ollama") and bare[1] == quickstart.OLLAMA_SERVE
    # a serving Ollama wins over a signed-in login: local, no account, no shared quota
    assert lines("r", _state(ollama="serving", ollama_model="m", claude=True)) == [add + "m"]
    for state in (_state(ollama="serving", ollama_model="m"), _state(ollama="no_model"),
                  _state(ollama="stopped"), _state()):
        for line in lines("r", state):
            assert "-free" not in line  # never another free model: same shared quota
            if "agent add opencode" in line:
                assert "--name local" in line  # the room's free agent is `<you>-opencode`


def test_every_prerequisite_agent_add_enforces_comes_first() -> None:
    """Codex on 0d4ce515: `agent_cmd._add` refuses without `quorus init` (the name and
    relay) and without the harness CLI on PATH, so the printed path must clear both."""
    lines = quickstart.next_command_lines
    # not set up yet: `quorus` first, whatever the machine has
    for state in (_state(ollama="serving", ollama_model="m"), _state(claude=True), _state()):
        state.configured = False
        assert lines("r", state)[0] == quickstart.QUORUS_SETUP
    assert lines("r", _state(claude=True))[0] == "quorus agent add claude --room r"  # set up
    # an ollama/* agent runs inside opencode: install it before the add
    without = lines("r", _state(ollama="serving", ollama_model="m", opencode=False))
    assert without == [quickstart.OPENCODE_INSTALL,
                       "quorus agent add opencode --room r --name local --model ollama/m"]
    bare = lines("r", _state(opencode=False))
    assert bare.index(quickstart.OPENCODE_INSTALL) < bare.index(quickstart.OLLAMA_SERVE)
    assert quickstart.OPENCODE_INSTALL not in lines("r", _state(ollama="stopped"))
    # a Gemini key file without the gemini CLI is not a path: fall through to Ollama
    assert lines("r", _state(gemini_env=True, gemini_cli=False))[0].startswith(
        "brew install ollama")
    assert lines("r", _state(gemini_env=True)) == \
        ["quorus agent add gemini --room r --model gemini-3.5-flash-lite"]


def test_every_caller_prints_the_same_next_command(monkeypatch) -> None:
    """One printer, so the demo and any later doctor use can never disagree."""
    state = _state(ollama="no_model")
    first, second = _C(), _C()
    quickstart.print_next_command(first, "general", state)
    quickstart.print_next_command(second, "general", state)
    assert first.out == second.out
    assert any("ollama pull qwen3:4b && quorus agent add opencode --room general --name local"
               in line for line in first.out)


def test_the_demos_all_busy_exit_prints_the_next_command(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from quorus_cli import demo_cmd

    monkeypatch.setattr(demo_cmd, "_opencode_ok", lambda: None)
    monkeypatch.setattr(demo_cmd, "pick_models", lambda *a, **k: [])
    monkeypatch.setattr(demo_cmd, "consented_fallback", lambda *a, **k: [])
    monkeypatch.setattr(quickstart, "machine_state",
                        lambda *a, **k: _state(ollama="serving", ollama_model="qwen3:4b"))
    console = _C()
    with pytest.raises(SystemExit) as exc:
        demo_cmd.cmd_demo(SimpleNamespace(model=None, timeout=1, keep=False,
                                          use_my_login=False), console)
    assert exc.value.code == 1
    assert any("quorus agent add opencode --room general --name local --model ollama/qwen3:4b"
               in line for line in console.out)


def test_the_gemini_check_only_asks_whether_the_file_exists(tmp_path: Path, monkeypatch) -> None:
    """Claude on 339b0e10: detection must never read `~/.gemini/.env`."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root can read a 000 file")
    (tmp_path / ".gemini").mkdir()
    env = tmp_path / ".gemini" / ".env"
    env.write_text("GEMINI_API_KEY=secret\n")
    env.chmod(0)  # a read would raise PermissionError; exists() does not
    try:
        monkeypatch.setattr(quickstart, "ollama_state", lambda *a, **k: ("absent", None))
        state = quickstart.machine_state(which=lambda b: None, home=tmp_path,
                                         config_file=tmp_path / "no-config.json")
    finally:
        env.chmod(0o600)
    assert state.gemini_env is True and state.ollama == "absent"
    assert state.claude is False and state.codex is False
    assert state.opencode is False and state.gemini_cli is False and state.configured is False


def test_ollama_state_reads_the_server_like_the_daemon(monkeypatch) -> None:
    import httpx

    def transport(handler):
        return monkeypatch.setattr(httpx, "get", handler)

    which = lambda b: "/bin/ollama" if b == "ollama" else None  # noqa: E731
    assert quickstart.ollama_state(lambda b: None) == ("absent", None)

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    transport(down)
    assert quickstart.ollama_state(which) == ("stopped", None)

    class _R:
        def __init__(self, models):
            self._m = models

        def raise_for_status(self):
            return None

        def json(self):
            return {"models": self._m}

    transport(lambda *a, **k: _R([]))
    assert quickstart.ollama_state(which) == ("no_model", None)
    transport(lambda *a, **k: _R([{"name": "qwen3:4b"}, {"name": "x"}]))
    assert quickstart.ollama_state(which) == ("serving", "qwen3:4b")


def _auth(stdout: str = "", rc: int = 0, exc: Exception | None = None):
    def run(cmd, **_kw):
        assert cmd[:3] == ["claude", "auth", "status"]
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")
    return run


def test_a_signed_out_claude_is_not_ready(tools, monkeypatch) -> None:
    """0.5.0 sweep: doctor said "Claude Code ready", then every wake answered
    "not logged in"."""
    monkeypatch.undo()
    monkeypatch.setattr(quickstart, "_codex_logged_in", lambda: True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(quickstart, "_run", _auth('{"loggedIn": false}', rc=1))
    claude = next(f for f in quickstart.detect(lambda b: f"/bin/{b}") if f.harness == "claude")
    assert not claude.ready and "claude /login" in claude.note
    assert any("claude /login" in w for w in agent_cmd._readiness_warnings("claude", None))


@pytest.mark.parametrize("run", [_auth('{"loggedIn": true}'), _auth(exc=FileNotFoundError()),
                                 _auth("error: unknown command 'auth'", rc=1)])
def test_signed_in_or_unknown_claude_is_never_blocked(monkeypatch, run) -> None:
    """An old Claude CLI without `auth status` can't tell; don't cry wolf."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(quickstart, "_run", run)
    assert quickstart._claude_logged_in() is not False
    assert not any("claude /login" in w for w in agent_cmd._readiness_warnings("claude", None))
