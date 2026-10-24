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
    assert any("Gemini CLI found but" in line for line in c.out)
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
           "PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    out = subprocess.run([sys.executable, "-m", "quorus_cli.cli", "doctor"], env=env,
                         capture_output=True, text=True, timeout=60, cwd=tmp_path)
    text = out.stdout + out.stderr
    assert "isn't set up yet" in text and "quorus demo" in text
    assert "--secret" not in text  # the old advice needed a secret nobody has yet
    # "no keys needed" over-promised: busy free models make the demo exit 1.
    flat = " ".join(text.split())  # rich wraps at the terminal width
    assert "no keys needed" not in flat and "retry later" in flat


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
