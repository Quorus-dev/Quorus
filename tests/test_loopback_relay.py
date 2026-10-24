"""A first-run profile points at http://localhost:8080; the laptop relay must be one
its owner is trusted on there, or `quorus agent add` is refused for the relay's own
owner (stranger-path test of 0.7.1, 2026-10-24)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from quorus.runtime.loopback import loopback_sockets


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


needs_v6 = pytest.mark.skipif(not _ipv6_loopback(), reason="no ::1 on this machine")


@needs_v6
def test_a_local_relay_takes_both_loopbacks_on_one_port() -> None:
    socks = loopback_sockets(0)
    try:
        assert [s.getsockname()[0] for s in socks] == ["127.0.0.1", "::1"]
        assert socks[0].getsockname()[1] == socks[1].getsockname()[1]
    finally:
        for s in socks:
            s.close()


@needs_v6
def test_a_held_ipv6_port_leaves_the_relay_on_ipv4_only() -> None:
    port = _free_port()
    with socket.socket(socket.AF_INET6) as squatter:
        squatter.bind(("::1", port))
        socks = loopback_sockets(port)
        try:
            assert [s.getsockname()[0] for s in socks] == ["127.0.0.1"]
        finally:
            for s in socks:
                s.close()


def _profile(port: int) -> dict:
    return {"relay_url": f"http://localhost:{port}", "relay_secret": "s" * 32,
            "api_key": "quorus_pk_" + "x" * 32, "instance_name": "sam"}


def test_an_ipv4_only_relay_still_leaves_localhost_untrusted(tmp_path, monkeypatch) -> None:
    """The exact 0.7.1 state, kept fail-closed: own_relays has only 127.0.0.1."""
    from quorus_cli import agent_cmd

    from quorus import relay as relay_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    relay_mod.record_own_relay([("127.0.0.1", 8080)])
    assert agent_cmd._on_teammates_relay(_profile(8080)) is True


@needs_v6
def test_the_first_run_relay_trusts_its_owner_on_localhost(tmp_path, monkeypatch) -> None:
    """Start the relay the way `quorus` does (HOST=127.0.0.1), then take agent add's
    credential path with a profile on localhost: the owner's agents get the relay's
    secret, not a refused member claim."""
    from quorus_cli import agent_cmd

    home, port = tmp_path / "home", _free_port()
    cfg = home / ".quorus"
    cfg.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "QUORUS_CONFIG_DIR": str(cfg),
           "RELAY_SECRET": "s" * 32, "PORT": str(port), "HOST": "127.0.0.1",
           "ALLOW_LEGACY_AUTH": "1", "MESSAGES_FILE": str(cfg / "relay-state.json"),
           "LOG_LEVEL": "WARNING"}
    proc = subprocess.Popen([sys.executable, "-m", "quorus.relay_cli"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                if httpx.get(f"http://[::1]:{port}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        for _ in range(40):
            own = cfg / "own_relays.json"
            if own.exists() and f"http://localhost:{port}" in json.loads(own.read_text()):
                break
            time.sleep(0.25)
        assert f"http://localhost:{port}" in json.loads((cfg / "own_relays.json").read_text())
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    monkeypatch.setattr(Path, "home", lambda: home)
    profile = _profile(port)
    assert agent_cmd._on_teammates_relay(profile) is False
    assert agent_cmd._credentials(profile, "sam-opencode", "opencode") == (
        profile["relay_secret"], True)
