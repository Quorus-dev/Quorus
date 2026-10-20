"""BUG 30 (0.6.3 multi-person audit): `quorus relay` is a wrapper around
`python -m quorus.relay_cli`. Killing the wrapper left the child serving (the port
stayed bound), and a second relay on a taken port died silently with exit 0 while
the old one kept answering. The wrapper now forwards SIGTERM/SIGINT and waits, and
refuses a bound port before forking.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHONPATH = os.pathsep.join(str(ROOT / p) for p in (
    ".", "packages/cli", "packages/mcp", "packages/sdk", "packages/tui"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _bindable(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def test_a_bound_port_is_refused_before_the_child_starts(monkeypatch, capsys, tmp_path):
    from quorus_cli import cli

    from quorus.runtime import local_relay

    monkeypatch.setenv("RELAY_SECRET", "test-secret")
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(local_relay, "_healthy", lambda url, timeout=2.0: False)  # not a relay
    started: list = []
    monkeypatch.setattr(cli, "_run_relay_child", lambda env: started.append(env) or 0)
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        with pytest.raises(SystemExit) as exc:
            cli._cmd_relay(SimpleNamespace(port=port, host="127.0.0.1", replace=False))
    finally:
        holder.close()
    assert exc.value.code == 1 and started == []
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert f"Port {port}" in text and "already in use" in text and "--port" in text


def test_a_child_that_exits_nonzero_is_reported_not_swallowed(monkeypatch, capsys, tmp_path):
    from quorus_cli import cli

    from quorus.runtime import local_relay

    monkeypatch.setenv("RELAY_SECRET", "test-secret")
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(local_relay, "_healthy", lambda url, timeout=2.0: False)
    monkeypatch.setattr(cli, "_port_holder", lambda host, port: None)
    monkeypatch.setattr(cli, "_run_relay_child", lambda env: 1)
    with pytest.raises(SystemExit) as exc:
        cli._cmd_relay(SimpleNamespace(port=_free_port(), host="127.0.0.1", replace=False))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "exited with code 1" in captured.out + captured.err


@pytest.mark.timeout(90)
def test_killing_the_wrapper_frees_the_port(tmp_path):
    """The overseer's acceptance test: SIGTERM to the wrapper PID, then the port is free."""
    port = _free_port()
    env = {**os.environ, "HOME": str(tmp_path), "QUORUS_CONFIG_DIR": str(tmp_path / ".quorus"),
           "RELAY_SECRET": "test-secret", "QUORUS_NO_LAUNCHD": "1", "QUORUS_NOTIFY": "0",
           "PYTHONPATH": PYTHONPATH, "PYTHONDONTWRITEBYTECODE": "1"}
    for k in ("RELAY_URL", "QUORUS_API_KEY", "MESSAGES_FILE"):
        env.pop(k, None)
    code = ("import sys; sys.argv = ['quorus', 'relay', '--port', '%d', '--host', '127.0.0.1']; "
            "from quorus_cli.cli import main; main()" % port)
    wrapper = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=str(tmp_path),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert wrapper.poll() is None, "the wrapper exited before the relay came up"
            time.sleep(0.3)
        else:
            pytest.fail("relay never answered /health")
        assert not _bindable(port)
        os.kill(wrapper.pid, signal.SIGTERM)  # the wrapper only, never its group
        wrapper.wait(timeout=20)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not _bindable(port):
            time.sleep(0.2)
        assert _bindable(port), "the relay child kept the port after its wrapper died"
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
        try:  # belt and braces: nothing of ours may survive the test
            os.killpg(wrapper.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
