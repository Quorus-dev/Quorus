"""`quorus agent` without launchd (Linux). First run on Linux, 2026-10-16:
`remove` crashed on the missing launchctl and left the daemon running, `list`
showed no agents, `add` claimed the agent "restarts at login", and a stale pid
file could make `remove` signal whatever process reused that pid."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from quorus_cli import agent_cmd, agent_proc

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *a: Any, **_k: Any) -> None:
        self.lines.append(" ".join(str(x) for x in a))


class _Args:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


@pytest.fixture
def linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(agent_cmd.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(agent_cmd.sys, "platform", "linux")
    monkeypatch.setattr(agent_cmd, "_leave_all_rooms", lambda name: [])
    monkeypatch.setattr(agent_cmd, "load_config", lambda: {"instance_name": "maya"})
    real_run = subprocess.run

    def no_launchctl(argv: list[str], *a: Any, **k: Any) -> Any:
        if argv and argv[0] == "launchctl":
            raise FileNotFoundError("launchctl")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(agent_cmd.subprocess, "run", no_launchctl)
    return agent_cmd._agents_dir()


def _fake_daemon(name: str, *, child: bool = False) -> subprocess.Popen:
    """A process whose argv looks like the real daemon's, in its own session."""
    code = ("import subprocess, sys, time\n"
            + ("subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
               if child else "")
            + "time.sleep(60)\n")
    return subprocess.Popen([sys.executable, "-c", code, "reflexd.py", "start",
                             "--participant", name], start_new_session=True)


def _wait_gone(pid: int) -> bool:
    for _ in range(50):
        if not agent_proc._alive(pid):
            return True
        time.sleep(0.1)
    return False


@posix_only
def test_remove_stops_the_daemon_and_its_children_without_launchctl(linux: Path) -> None:
    proc = _fake_daemon("maya-opencode", child=True)
    try:
        agent_proc.pidfile(linux, "maya-opencode").write_text(str(proc.pid))
        time.sleep(0.3)
        kids = subprocess.run(["pgrep", "-g", str(proc.pid)], capture_output=True,
                              text=True).stdout.split()
        console = _Console()
        agent_cmd.cmd_agent(_Args(agent_action="remove", name="maya-opencode"), console)
        proc.wait(timeout=10)
        assert any("stopped @maya-opencode" in line for line in console.lines)
        assert not agent_proc.pidfile(linux, "maya-opencode").exists()
        for kid in kids:  # the agent CLI it was running must not be orphaned
            assert _wait_gone(int(kid)), f"orphan {kid}"
    finally:
        if proc.poll() is None:
            proc.kill()


@posix_only
def test_stale_pid_file_never_signals_an_unrelated_process(linux: Path) -> None:
    # After a reboot the pid in the file can belong to anything.
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        agent_proc.pidfile(linux, "maya-opencode").write_text(str(other.pid))
        assert agent_proc.live_pid(linux, "maya-opencode") is None
        assert agent_proc.stop(linux, "maya-opencode") is False
        assert other.poll() is None  # still alive
    finally:
        other.kill()


@posix_only
def test_list_shows_running_and_stopped_agents_from_pid_files(linux: Path) -> None:
    proc = _fake_daemon("maya-opencode")
    try:
        agent_proc.pidfile(linux, "maya-opencode").write_text(str(proc.pid))
        agent_proc.pidfile(linux, "maya-review-opencode").write_text("999999")
        agent_proc.pidfile(linux, "bob-codex").write_text(str(proc.pid))  # not mine
        console = _Console()
        agent_cmd._list(console)
        text = "\n".join(console.lines)
        assert f"@maya-opencode  [dim]pid {proc.pid}" in text
        assert "@maya-review-opencode" in text and "stopped" in text
        assert "bob-codex" not in text
    finally:
        proc.kill()


@posix_only
def test_running_daemon_found_from_pid_file_without_pgrep(linux: Path,
                                                          monkeypatch) -> None:
    # python:3.12-slim has no pgrep: a second `agent add` started a duplicate.
    proc = _fake_daemon("maya-opencode")
    try:
        agent_proc.pidfile(linux, "maya-opencode").write_text(str(proc.pid))
        assert agent_cmd._running_daemon_for("maya-opencode") == f"pid {proc.pid}"
    finally:
        proc.kill()


def test_add_on_linux_does_not_claim_it_restarts_at_login(linux: Path,
                                                          monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd, "load_config", lambda: {
        "instance_name": "maya", "relay_url": "http://r", "relay_secret": "s"})
    monkeypatch.setattr(agent_cmd.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(agent_cmd, "_readiness_warnings", lambda h, m: [])
    monkeypatch.setattr(agent_cmd, "_join", lambda *a: None)
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda name: None)
    monkeypatch.setattr(agent_cmd, "_start_daemon", lambda *a, **k: "pid 42")
    console = _Console()
    agent_cmd.cmd_agent(_Args(agent_action="add", tool="codex", room="build", repo=None,
                              mode=None, name=None, model=None), console)
    text = "\n".join(console.lines)
    assert "restarts at login" not in text
    assert "not after a reboot" in text
