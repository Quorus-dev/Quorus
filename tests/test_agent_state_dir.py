"""Never Stuck 2c-4: `quorus agent add --state-dir DIR` (dogfood runbook §2).

The dogfood agents run beside the team's on one HOME. With --state-dir, the daemon
gets QUORUS_CONFIG_DIR=DIR, so its bindings, runtime, journals and holds live there,
and the --repo binding is written to DIR's file, never to the owner's. The CLI
itself keeps reading the owner's relay config.
"""

from __future__ import annotations

import json
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
