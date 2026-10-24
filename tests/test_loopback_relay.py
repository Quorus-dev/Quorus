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


def _start_relay(home: Path, port: int) -> subprocess.Popen:
    cfg = home / ".quorus"
    cfg.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "QUORUS_CONFIG_DIR": str(cfg),
           "RELAY_SECRET": "s" * 32, "PORT": str(port), "HOST": "127.0.0.1",
           "ALLOW_LEGACY_AUTH": "1", "MESSAGES_FILE": str(cfg / "relay-state.json"),
           "LOG_LEVEL": "WARNING"}
    proc = subprocess.Popen([sys.executable, "-m", "quorus.relay_cli"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    own = cfg / "own_relays.json"
    for _ in range(80):
        if own.exists() and f"http://127.0.0.1:{port}" in json.loads(own.read_text()):
            return proc
        time.sleep(0.25)
    proc.terminate()
    raise AssertionError("the relay never recorded itself")


@needs_v6
def test_agent_add_works_on_an_ipv4_only_relay_from_a_localhost_profile(
        tmp_path, monkeypatch) -> None:
    """Codex on 21d422ff: with ::1 taken the relay holds only 127.0.0.1, and the
    first-run profile says localhost. `agent add` must still add the owner's agent,
    pointed at the address the relay owns, without trusting `localhost`."""
    import types

    from quorus_cli import agent_cmd

    from quorus.config import ConfigManager

    home, port = tmp_path / "home", _free_port()
    with socket.socket(socket.AF_INET6) as squatter:
        squatter.bind(("::1", port))  # another process holds ::1 on this port
        proc = _start_relay(home, port)
        try:
            cfg_dir = home / ".quorus"
            assert f"http://localhost:{port}" not in json.loads(
                (cfg_dir / "own_relays.json").read_text())
            monkeypatch.setenv("HOME", str(home))
            monkeypatch.setenv("QUORUS_CONFIG_DIR", str(cfg_dir))
            monkeypatch.setattr(Path, "home", lambda: home)
            for k in ("RELAY_URL", "RELAY_SECRET", "QUORUS_API_KEY", "QUORUS_PROFILE"):
                monkeypatch.delenv(k, raising=False)
            ConfigManager(cfg_dir / "config.json").save({
                "relay_url": f"http://localhost:{port}", "instance_name": "sam",
                "relay_secret": "s" * 32, "api_key": "quorus_pk_" + "x" * 32})
            base = f"http://127.0.0.1:{port}"
            auth = {"Authorization": "Bearer " + "s" * 32}
            assert httpx.post(f"{base}/rooms", json={"name": "general", "created_by": "sam"},
                              headers=auth, timeout=10).status_code in (200, 201)

            started = []
            monkeypatch.setattr(agent_cmd.shutil, "which", lambda h: f"/bin/{h}")
            monkeypatch.setattr(agent_cmd, "_start_daemon",
                                lambda cfg, name, cred, legacy, *a: started.append(
                                    (cfg["relay_url"], name, legacy)) or "pid 1")
            monkeypatch.setattr("quorus_cli.agent_declare.declare", lambda *a, **k: True)
            args = types.SimpleNamespace(tool="opencode", room="general", repo=None,
                                         mode=None, name=None, model=None, lead=False,
                                         trust=None, state_dir=None, disclosed=False)
            agent_cmd._add(args, types.SimpleNamespace(print=lambda *a, **k: None))

            assert started == [(base, "sam-opencode", True)]
            rooms = httpx.get(f"{base}/rooms", headers=auth, timeout=10).json()
            members = next(r for r in rooms if r["name"] == "general")["members"]
            assert "sam-opencode" in members
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def test_owned_alias_only_maps_localhost_to_an_owned_loopback(tmp_path) -> None:
    from quorus.runtime import own_relays

    path = tmp_path / "own.json"
    own_relays.add_own_relay(path, "http://127.0.0.1:8080")
    alias = own_relays.owned_alias
    assert alias("http://localhost:8080", path) == "http://127.0.0.1:8080"
    assert alias("http://localhost:9090", path) == "http://localhost:9090"  # other port
    assert alias("https://localhost:8080", path) == "https://localhost:8080"  # scheme
    assert alias("http://relay.example.com:8080", path) == "http://relay.example.com:8080"
    own_relays.add_own_relay(path, "http://localhost:8080")
    assert alias("http://localhost:8080", path) == "http://localhost:8080"  # both held


def test_the_wizard_saves_the_address_its_relay_owns(tmp_path, monkeypatch) -> None:
    from quorus_tui import hub

    from quorus.runtime import own_relays

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    own_relays.add_own_relay(own_relays.own_relays_path(), "http://127.0.0.1:8080")
    assert hub._owned_relay_url("http://localhost:8080", wait_s=0) == "http://127.0.0.1:8080"
    own_relays.add_own_relay(own_relays.own_relays_path(), "http://localhost:8080")
    assert hub._owned_relay_url("http://localhost:8080", wait_s=0) == "http://localhost:8080"
