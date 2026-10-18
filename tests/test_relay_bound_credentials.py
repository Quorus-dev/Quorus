"""A saved credential only ever goes to the relay it was saved for.

Reproduced on the 0.5.1 wheel: ~/.quorus/config.json held relay A's api_key,
QUORUS_RELAY_URL pointed at relay B, and quorus-mcp POSTed A's key to B's
/v1/auth/token. The same held for the legacy RELAY_URL override and for
`quorus join --relay B` without --secret.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from quorus.config import load_config, same_relay

RELAY_A = "http://127.0.0.1:8080"
RELAY_B = "https://relay-b.example"
KEY_A = "mct_aa_bb"
SECRET_A = "secret-for-a"
_ENV = ("QUORUS_RELAY_URL", "QUORUS_RELAY_SECRET", "QUORUS_API_KEY", "QUORUS_INSTANCE_NAME",
        "QUORUS_PROFILE", "RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME",
        "MCP_TUNNEL_CONFIG_DIR")


@pytest.fixture
def saved(tmp_path, monkeypatch):
    """A profile saved for relay A, with both an API key and a shared secret."""
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config.json").write_text(json.dumps(
        {"relay_url": RELAY_A, "api_key": KEY_A, "relay_secret": SECRET_A,
         "instance_name": "alice"}))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def no_network(monkeypatch):
    """Record (and refuse) every outbound request, to assert zero sends."""
    sent: list[tuple[str, str]] = []

    def refuse(method):
        def call(url, *a, **kw):
            sent.append((method, str(url)))
            raise AssertionError(f"unexpected {method} {url}")
        return call

    for method in ("get", "post", "request"):
        monkeypatch.setattr(httpx, method, refuse(method))
    return sent


# ── core config resolution ───────────────────────────────────────────────


def test_legacy_relay_url_override_withholds_saved_credentials(saved, monkeypatch):
    monkeypatch.setenv("RELAY_URL", RELAY_B)
    cfg = load_config()
    assert cfg["relay_url"] == RELAY_B
    assert cfg["api_key"] == "" and cfg["relay_secret"] == ""
    assert cfg["credentials_withheld"] and cfg["saved_relay_url"] == RELAY_A


@pytest.mark.parametrize("url", [None, RELAY_A, RELAY_A + "/", "HTTP://127.0.0.1:8080"])
def test_matching_relay_keeps_saved_credentials(saved, monkeypatch, url):
    if url:
        monkeypatch.setenv("RELAY_URL", url)
    cfg = load_config()
    assert cfg["api_key"] == KEY_A and cfg["relay_secret"] == SECRET_A
    assert not cfg["credentials_withheld"]


def test_env_credentials_for_another_relay_still_work(saved, monkeypatch):
    monkeypatch.setenv("RELAY_URL", RELAY_B)
    monkeypatch.setenv("API_KEY", "mct_bb_cc")
    cfg = load_config()
    assert cfg["api_key"] == "mct_bb_cc" and cfg["relay_secret"] == ""


def test_same_relay():
    assert same_relay("https://h.example", "https://h.example:443/")
    assert not same_relay("https://h.example/team-a", "https://h.example/team-b")
    assert not same_relay("http://h.example", "https://h.example")
    assert not same_relay("http://h:99999", "http://h")


# ── quorus-mcp ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("override", ["QUORUS_RELAY_URL", "RELAY_URL"])
def test_mcp_refuses_before_sending_anything(saved, no_network, monkeypatch, override):
    from quorus_mcp import runtime

    monkeypatch.setenv(override, RELAY_B)
    monkeypatch.setenv("QUORUS_API_KEY", "")  # a plugin's blank field
    with pytest.raises(SystemExit, match=f"no credential saved for {RELAY_B}"):
        runtime.load_runtime_config()
    assert no_network == []


def test_mcp_uses_explicit_credentials_for_the_new_relay(saved, monkeypatch):
    from quorus_mcp import runtime

    monkeypatch.setenv("QUORUS_RELAY_URL", RELAY_B)
    monkeypatch.setenv("QUORUS_RELAY_SECRET", "secret-for-b")
    settings = runtime.load_runtime_config()
    assert settings["relay_secret"] == "secret-for-b" and settings["api_key"] == ""


def test_mcp_matching_relay_works(saved, monkeypatch):
    from quorus_mcp import runtime

    monkeypatch.setenv("QUORUS_RELAY_URL", RELAY_A + "/")
    settings = runtime.load_runtime_config()
    assert settings["api_key"] == KEY_A


def test_mcp_profile_is_bound_the_same_way(tmp_path, monkeypatch, no_network):
    from quorus_mcp import runtime

    from quorus.config import ConfigManager

    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    ConfigManager(profile="work").save({"relay_url": RELAY_A, "api_key": KEY_A,
                                        "instance_name": "alice"})
    monkeypatch.setenv("QUORUS_PROFILE", "work")
    assert runtime.load_runtime_config()["api_key"] == KEY_A
    monkeypatch.setenv("QUORUS_RELAY_URL", RELAY_B)
    with pytest.raises(SystemExit, match="no credential saved"):
        runtime.load_runtime_config()
    assert no_network == []


# ── quorus join ──────────────────────────────────────────────────────────


def _join_args(**kw):
    base = dict(token="", relay_url=None, secret=None, api_key=None, room="dev",
                name="alice")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def cli_saved(monkeypatch):
    from quorus_cli import cli

    monkeypatch.setattr(cli, "RELAY_URL", RELAY_A)
    monkeypatch.setattr(cli, "RELAY_SECRET", SECRET_A)
    monkeypatch.setattr(cli, "API_KEY", "")
    monkeypatch.setattr(cli, "_config", {"relay_url": RELAY_A, "saved_relay_url": RELAY_A,
                                         "relay_secret": SECRET_A, "api_key": ""})
    return cli


def test_join_with_another_relay_never_sends_the_saved_secret(cli_saved, no_network,
                                                              capsys):
    with pytest.raises(SystemExit) as exit_:
        cli_saved._cmd_join(_join_args(relay_url=RELAY_B))
    assert exit_.value.code == 3
    assert no_network == []
    assert "no credential saved for" in capsys.readouterr().out.replace("\n", " ")


# ── the wake daemon ──────────────────────────────────────────────────────


def _reflexd():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "reflexd.py"
    spec = importlib.util.spec_from_file_location("reflexd_relay_bound", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reflexd_relay_bound"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("override", ["REFLEXD_RELAY_URL", "RELAY_URL"])
def test_reflexd_never_takes_the_saved_key_to_another_relay(saved, monkeypatch, override):
    reflexd = _reflexd()
    monkeypatch.setenv(override, RELAY_B)
    with pytest.raises(SystemExit, match=f"no credential saved for {RELAY_B}"):
        reflexd.ReflexdConfig.from_env()
    monkeypatch.setenv("REFLEXD_API_KEY", "mct_bb_cc")
    assert reflexd.ReflexdConfig.from_env().api_key == "mct_bb_cc"
    monkeypatch.delenv("REFLEXD_API_KEY")
    monkeypatch.setenv(override, RELAY_A + "/")
    assert reflexd.ReflexdConfig.from_env().api_key == KEY_A
