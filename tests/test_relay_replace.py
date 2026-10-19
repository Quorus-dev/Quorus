"""`quorus relay --host 0.0.0.0` beside the relay the first run started.

The first `quorus` run starts a relay in the background on 127.0.0.1:8080. The
deployment guide's LAN step then runs `quorus relay --host 0.0.0.0 --port 8080`:
on macOS that binds beside the first one (two relays, one state file), on Linux
it fails. Now it refuses, and --replace stops only the relay quorus started.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time

import pytest

from quorus.runtime import local_relay


@pytest.fixture(autouse=True)
def _config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RELAY_SECRET", "probe-secret")


def _args(**kw):
    return argparse.Namespace(port=18181, host="0.0.0.0", replace=False, **kw)


def test_it_refuses_while_a_relay_answers_on_that_port(monkeypatch, capsys):
    from quorus_cli import cli

    monkeypatch.setattr(local_relay, "_healthy", lambda url, timeout=2.0: True)
    started = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: started.append(a))
    with pytest.raises(SystemExit):
        cli._cmd_relay(_args())
    assert started == []
    assert "--replace" in " ".join(capsys.readouterr().out.split())


def test_replace_refuses_a_relay_quorus_did_not_start(monkeypatch, tmp_path):
    import json

    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "relay.pid").write_text(json.dumps(
            {"pid": other.pid, "port": 18181, "started": local_relay._started(other.pid),
             "command": local_relay._ps(other.pid, "command")}))
        assert local_relay.stop_started_relay(18181) is False
        assert other.poll() is None  # still running: not ours to kill
    finally:
        other.kill()


RECORDED = "/opt/py/bin/python3 -m quorus.relay_cli"


@pytest.mark.parametrize(("command", "started", "signalled"), [
    # codex, review of 857a5271: a mere mention of quorus.relay_cli was enough.
    ("/usr/bin/vim notes-on-quorus.relay_cli.md", "Sat Oct 18 10:00:00 2026", False),
    ("/usr/bin/python3 -c import quorus.relay_cli", "Sat Oct 18 10:00:00 2026", False),
    # codex, review of 56f6b852: an interpreter running something else, ending the same way
    ("/opt/py/bin/python3 -c pass # -m quorus.relay_cli", "Sat Oct 18 10:00:00 2026", False),
    ("/usr/bin/sh -m quorus.relay_cli", "Sat Oct 18 10:00:00 2026", False),
    # the exact command, but the pid was reused by a process started later
    (RECORDED, "Sat Oct 18 11:11:11 2026", False),
    (RECORDED, "Sat Oct 18 10:00:00 2026", True),
])
def test_replace_signals_only_the_exact_process_it_started(
        monkeypatch, tmp_path, command, started, signalled):
    (tmp_path / "relay.pid").write_text(json.dumps(
        {"pid": 4242, "port": 18181, "started": "Sat Oct 18 10:00:00 2026",
         "command": RECORDED}))
    sent = []
    # Once signalled, the process is gone: ps shows nothing for its pid.
    monkeypatch.setattr(local_relay, "_ps", lambda pid, field: "" if sent else (
        command if field == "command" else started))
    monkeypatch.setattr(local_relay.os, "kill", lambda pid, sig: sent.append(pid))
    monkeypatch.setattr(local_relay, "_healthy", lambda url, timeout=2.0: False)
    local_relay.stop_started_relay(18181)
    assert sent == ([4242] if signalled else [])


def test_replace_waits_for_the_old_relay_to_exit_not_just_go_quiet(monkeypatch, tmp_path):
    """uvicorn stops answering at once but saves the relay's state only when its
    streams close: a replacement started in between loaded the old state file and
    the first run's rooms were lost (founding scenario test)."""
    (tmp_path / "relay.pid").write_text(json.dumps(
        {"pid": 4242, "port": 18181, "started": "Sat Oct 18 10:00:00 2026",
         "command": RECORDED}))
    polls = []

    def ps(pid, field):  # still shutting down for the first three looks after SIGTERM
        if field == "command":
            polls.append(pid)
            return RECORDED if len(polls) <= 4 else ""
        return "Sat Oct 18 10:00:00 2026"

    monkeypatch.setattr(local_relay, "_ps", ps)
    monkeypatch.setattr(local_relay.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(local_relay, "_healthy", lambda url, timeout=2.0: False)
    monkeypatch.setattr(local_relay.time, "sleep", lambda s: None)
    assert local_relay.stop_started_relay(18181) is True
    assert len(polls) == 5  # the identity check before SIGTERM, then 3 alive, 1 gone
    assert not (tmp_path / "relay.pid").exists()

    (tmp_path / "relay.pid").write_text(json.dumps(
        {"pid": 4242, "port": 18181, "started": "Sat Oct 18 10:00:00 2026",
         "command": RECORDED}))
    monkeypatch.setattr(local_relay, "_ps", lambda pid, field: (
        RECORDED if field == "command" else "Sat Oct 18 10:00:00 2026"))
    assert local_relay.stop_started_relay(18181, timeout=0.05) is False  # never exited


def test_a_pid_file_recording_a_disguised_command_is_refused(monkeypatch, tmp_path):
    """Even if relay.pid itself names `python3 -c ... # -m quorus.relay_cli`."""
    disguised = "/opt/py/bin/python3 -c pass # -m quorus.relay_cli"
    (tmp_path / "relay.pid").write_text(json.dumps(
        {"pid": 4242, "port": 18181, "started": "x", "command": disguised}))
    monkeypatch.setattr(local_relay, "_ps",
                        lambda pid, field: disguised if field == "command" else "x")
    sent = []
    monkeypatch.setattr(local_relay.os, "kill", lambda pid, sig: sent.append(pid))
    local_relay.stop_started_relay(18181)
    assert sent == []


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_replace_stops_the_relay_the_first_run_started(tmp_path):
    port = _free_port()
    ok, _ = local_relay.ensure_local_relay(f"http://127.0.0.1:{port}", "probe-secret")
    assert ok, (tmp_path / "relay.log").read_text() if (tmp_path / "relay.log").exists() else ""
    pid = int(json.loads((tmp_path / "relay.pid").read_text())["pid"])
    try:
        assert local_relay.stop_started_relay(port) is True
        assert not local_relay._healthy(f"http://127.0.0.1:{port}", 0.5)
        assert not (tmp_path / "relay.pid").exists()
    finally:
        try:
            import os
            os.kill(pid, 9)
        except OSError:
            pass
        time.sleep(0.1)
