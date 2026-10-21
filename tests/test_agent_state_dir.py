"""Never Stuck 2c-4: `quorus agent add --state-dir DIR` (dogfood runbook §2).

The dogfood agents run beside the team's on one HOME. With --state-dir, the daemon
gets QUORUS_CONFIG_DIR=DIR, so its bindings, runtime, journals and holds live there,
and the --repo binding is written to DIR's file, never to the owner's. The CLI
itself keeps reading the owner's relay config.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
from quorus_cli import agent_cmd

from tests.test_agent_cmd import _Args, _Console, env  # noqa: F401  (fixture)


def test_the_binding_lands_in_the_state_dir_not_the_owners(env, tmp_path):  # noqa: F811
    repo, state = tmp_path / "dogfood", tmp_path / "qd" / "claude"
    repo.mkdir()
    agent_cmd.cmd_agent(_Args(name="handoff", room="weekend-handoff", repo=str(repo),
                              mode="autonomous", state_dir=str(state)), _Console())
    assert env["joins"][-1][1] == "arav-handoff-claude"
    assert env["state_dir"] == state
    mine = json.loads((state / "room-bindings.json").read_text())
    assert mine["weekend-handoff"]["path"] == str(repo)
    owner = agent_cmd.bindings_path()
    assert not owner.exists() or "weekend-handoff" not in json.loads(owner.read_text())
    assert state.stat().st_mode & 0o777 == 0o700


def test_without_it_nothing_changes(env, tmp_path):  # noqa: F811
    repo = tmp_path / "app"
    repo.mkdir()
    agent_cmd.cmd_agent(_Args(repo=str(repo)), _Console())
    assert env["state_dir"] is None
    assert "build" in json.loads(agent_cmd.bindings_path().read_text())


@pytest.mark.parametrize("raw, why", [("relative/dir", "absolute"), (None, "separate")])
def test_bad_state_dirs_are_refused(env, monkeypatch, tmp_path, raw, why):  # noqa: F811
    if raw is None:
        from quorus.runtime.bindings import config_dir

        raw = str(config_dir())
    with pytest.raises(SystemExit, match=why):
        agent_cmd.cmd_agent(_Args(state_dir=raw), _Console())
    assert env["joins"] == []  # refused before joining anything


def test_the_daemon_gets_the_state_dir_as_its_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(agent_cmd, "_LA", tmp_path / "LA")
    monkeypatch.setattr(agent_cmd.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(sys, "platform", "darwin")
    agent_cmd._start_daemon({"relay_url": "http://r"}, "arav-handoff-claude", "k", True,
                            None, tmp_path / "qd" / "claude")
    xml = (tmp_path / "LA" / "dev.quorus.agent.arav-handoff-claude.plist").read_text()
    assert f"<key>QUORUS_CONFIG_DIR</key><string>{tmp_path / 'qd' / 'claude'}" in xml
    agent_cmd._start_daemon({"relay_url": "http://r"}, "arav-claude", "k", True)
    plain = (tmp_path / "LA" / "dev.quorus.agent.arav-claude.plist").read_text()
    assert "QUORUS_CONFIG_DIR" not in plain  # the team's daemons: unchanged


def test_the_flag_is_on_the_cli():
    import subprocess

    out = subprocess.run([sys.executable, "-c", "import sys; from quorus_cli.cli import main; "
                          "sys.argv=['quorus','agent','add','--help']; main()"],
                         capture_output=True, text=True, timeout=60)
    assert "--state-dir" in out.stdout, out.stderr[-300:]


def test_the_agents_own_tool_wins_on_its_daemon_path(tmp_path, monkeypatch):
    """Codex on 826faa27: with a fakebin `claude` first on PATH and the real claude
    beside codex elsewhere, the daemon's PATH put codex's dir (and its claude) first."""
    import shutil

    fake, real = tmp_path / "fakebin", tmp_path / "real"
    for d, names in ((fake, ["claude"]), (real, ["claude", "codex"])):
        d.mkdir()
        for n in names:
            (d / n).write_text("#!/bin/sh\n")
            (d / n).chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{real}")
    path = agent_cmd._harness_path("claude")
    assert shutil.which("claude", path=path) == str(fake / "claude")
    assert shutil.which("codex", path=path) == str(real / "codex")
    # without an own tool, the old order (unchanged for the team's daemons)
    assert shutil.which("claude", path=agent_cmd._harness_path()) == str(real / "claude")


@pytest.mark.parametrize("running_in, asked", [(None, "qd/claude"), ("qd/claude", None),
                                               ("qd/old", "qd/claude")])
def test_a_running_daemon_with_other_state_is_refused(env, monkeypatch, tmp_path,  # noqa: F811
                                                      running_in, asked):
    """Codex on 246acab9: a running daemon accepted a new --state-dir, bound the repo
    into it and reported success, while the daemon kept reading its old state."""
    repo = tmp_path / "dogfood"
    repo.mkdir()
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda *a, **k: "pid 42")
    if running_in:
        agent_cmd._state_record("arav-handoff-claude").write_text(str(tmp_path / running_in))
    with pytest.raises(SystemExit, match="agent remove arav-handoff-claude"):
        agent_cmd.cmd_agent(_Args(name="handoff", room="weekend-handoff", repo=str(repo),
                                  state_dir=str(tmp_path / asked) if asked else None),
                            _Console())
    assert env["joins"] == [] and env["daemons"] == []  # refused before any change
    for d in (tmp_path / "qd" / "claude", tmp_path / "qd" / "old"):
        assert not (d / "room-bindings.json").exists()
    owner = agent_cmd.bindings_path()
    assert not owner.exists() or "weekend-handoff" not in json.loads(owner.read_text())


def test_a_running_daemon_with_the_same_state_is_reused(env, monkeypatch, tmp_path):  # noqa: F811
    state = tmp_path / "qd" / "codex"
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda *a, **k: "pid 42")
    agent_cmd._state_record("arav-handoff-codex").write_text(str(state))
    console = _Console()
    agent_cmd.cmd_agent(_Args(tool="codex", name="handoff", room="weekend-handoff-rehearsal",
                              state_dir=str(state)), console)
    assert env["joins"][-1][:2] == ("weekend-handoff-rehearsal", "arav-handoff-codex")
    assert env["daemons"] == [] and any("already running" in x for x in console.lines)


def test_starting_and_removing_keep_the_state_record(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(agent_cmd, "_LA", tmp_path / "LA")
    monkeypatch.setattr(agent_cmd.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(sys, "platform", "darwin")
    state = tmp_path / "qd" / "claude"
    agent_cmd._start_daemon({"relay_url": "http://r"}, "arav-handoff-claude", "k", True,
                            None, state)
    assert agent_cmd._daemon_state_dir("arav-handoff-claude") == state
    agent_cmd._start_daemon({"relay_url": "http://r"}, "arav-handoff-claude", "k", True)
    assert agent_cmd._daemon_state_dir("arav-handoff-claude") is None
