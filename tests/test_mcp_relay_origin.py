"""An unreachable relay says where its URL came from and how to change it.

Arav hit "Cannot reach relay server at http://localhost:8080" from a stale project
.mcp.json, and the error didn't say the URL came from there (overseer, 2026-10-28).
"""

from __future__ import annotations

import json

import httpx
import pytest
from quorus_mcp import runtime, server

_ENV = ("QUORUS_RELAY_URL", "QUORUS_RELAY_SECRET", "QUORUS_API_KEY", "QUORUS_INSTANCE_NAME",
        "QUORUS_PROFILE", "RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME",
        "MCP_TUNNEL_CONFIG_DIR")


@pytest.fixture
def home(tmp_path, monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _save(home, **cfg):
    (home / "config.json").write_text(json.dumps({"instance_name": "alice", **cfg}))


def test_from_the_mcp_entry(home, monkeypatch):
    _save(home, relay_url="http://saved.test", api_key="mct_aa_bb")
    monkeypatch.setenv("QUORUS_RELAY_URL", "http://localhost:8080")
    monkeypatch.setenv("QUORUS_RELAY_SECRET", "s")
    origin = runtime.load_runtime_config()["relay_url_origin"]
    assert "QUORUS_RELAY_URL" in origin and ".mcp.json" in origin


def test_from_the_saved_config(home):
    _save(home, relay_url="http://saved.test", api_key="mct_aa_bb")
    origin = runtime.load_runtime_config()["relay_url_origin"]
    assert str(home / "config.json") in origin and "relay_url" in origin


def test_from_the_default(home, monkeypatch):
    _save(home)
    monkeypatch.setenv("QUORUS_RELAY_SECRET", "s")
    settings = runtime.load_runtime_config()
    assert settings["relay_url"] == "http://localhost:8080"
    assert "built-in default" in settings["relay_url_origin"]


def test_the_tool_error_names_the_origin(monkeypatch):
    monkeypatch.setattr(server, "RELAY_URL", "http://localhost:8080")
    monkeypatch.setattr(server, "RELAY_URL_ORIGIN", runtime.relay_url_origin(
        "env:QUORUS_RELAY_URL", "/x/config.json"))
    msg = server._relay_error_message(httpx.ConnectError("refused"))
    assert msg.startswith("Error: Cannot reach relay server at http://localhost:8080.")
    assert "QUORUS_RELAY_URL" in msg and "restart the client" in msg
