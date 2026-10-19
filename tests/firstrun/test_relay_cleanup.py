"""Relays a first run starts never outlive the test or the wizard (codex, review
of e409ee1a): a startup timeout stops the relay it spawned, and the pty harness
stops every relay its HOME started, pid file or not."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx
import pytest

pytest.importorskip("fcntl", reason="needs a POSIX pty")
from quorus.runtime import local_relay
from tests.firstrun.pty_session import free_port, spawned_relays, stop_home_relays

pytestmark = pytest.mark.firstrun


def test_a_relay_that_never_answers_is_stopped(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(local_relay, "_healthy", lambda *a, **k: False)
    monkeypatch.setattr(local_relay.time, "sleep", lambda _s: None)
    spawned: list[subprocess.Popen] = []
    real = subprocess.Popen

    def fake_popen(argv, **kw):
        # A process that never answers, standing in for a relay stuck at startup.
        proc = real([sys.executable, "-c", "import time; time.sleep(60)"], **kw)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(local_relay.subprocess, "Popen", fake_popen)
    ok, note = local_relay.ensure_local_relay(f"http://127.0.0.1:{free_port()}", "s")
    assert not ok and "didn't start" in note
    assert spawned and spawned[0].poll() is not None
    assert not (tmp_path / "cfg" / "relay.pid").exists()


def test_the_harness_stops_a_relay_its_pid_file_missed(tmp_path) -> None:
    home = (tmp_path / "home").resolve()
    (home / ".quorus").mkdir(parents=True)
    port = free_port()
    env = {**{k: v for k, v in os.environ.items() if k != "RELAY_URL"},
           "RELAY_SECRET": "s", "PORT": str(port), "HOST": "127.0.0.1",
           "MESSAGES_FILE": str(home / ".quorus" / "relay-state.json"), "LOG_LEVEL": "INFO"}
    with (home / ".quorus" / "relay.log").open("ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "quorus.relay_cli"], env=env, cwd=home,
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not spawned_relays(home):
            time.sleep(0.2)
        assert spawned_relays(home) == [proc.pid]
        stop_home_relays(home, port)  # no relay.pid: found through the log
        proc.wait(timeout=5)
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=1, trust_env=False)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
