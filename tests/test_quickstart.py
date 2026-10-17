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
    added = quickstart.offer(c, "general", cwd=tmp_path, ask=lambda q: True,
                             add=lambda a, _c: calls.append(a), which=tools)
    assert added == ["claude", "codex", "opencode"]
    assert [a.lead for a in calls] == [True, False, False]
    assert {a.repo for a in calls} == {str(tmp_path.resolve())}
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
                             which=tools)
    assert added == ["claude", "opencode"]


def test_no_repo_means_chat_only_and_one_agent_has_no_lead(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(agent_cmd, "_opencode_version", lambda: "2.1.0")
    calls: list[Any] = []
    quickstart.offer(_C(), "general", cwd=tmp_path, ask=lambda q: True,
                     add=lambda a, _c: calls.append(a),
                     which=lambda b: "/bin/opencode" if b == "opencode" else None)
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
