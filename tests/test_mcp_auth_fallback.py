"""Regression: empty/whitespace QUORUS_API_KEY must fall through to profile.

Codex audit (2026-05-03): stale ``QUORUS_API_KEY=`` (empty) caused the MCP
module to send ``Authorization: Bearer `` (HTTP 400 "Illegal header value").
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def _seed(d: Path, key: str) -> None:
    """Write profile JSON + pointer (~/.quorus/config.json) for ConfigManager."""
    (d / "profiles").mkdir(parents=True, exist_ok=True)
    (d / "profiles" / "default.json").write_text(
        json.dumps({"api_key": key, "relay_url": "https://relay.test"})
    )
    (d / "config.json").write_text(
        json.dumps({"current": "default", "profiles": ["default"]})
    )


def _reload(monkeypatch, tmp: Path, env: dict[str, str]):
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp))
    for k in ("QUORUS_API_KEY", "QUORUS_RELAY_SECRET", "QUORUS_PROFILE",
              "QUORUS_RELAY_URL", "RELAY_URL", "RELAY_SECRET", "API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import quorus_mcp.server as srv
    return importlib.reload(srv)


def test_mcp_falls_through_to_profile_when_env_empty(monkeypatch, tmp_path):
    _seed(tmp_path, "mct_profile_value")
    srv = _reload(monkeypatch, tmp_path, {"QUORUS_API_KEY": ""})
    assert srv.API_KEY == "mct_profile_value"


def test_mcp_falls_through_when_env_whitespace(monkeypatch, tmp_path):
    _seed(tmp_path, "mct_profile_value")
    srv = _reload(monkeypatch, tmp_path, {"QUORUS_API_KEY": "  \t "})
    assert srv.API_KEY == "mct_profile_value"
    assert srv.API_KEY.strip() == srv.API_KEY


def test_mcp_uses_env_when_set(monkeypatch, tmp_path):
    _seed(tmp_path, "mct_profile_value")
    srv = _reload(monkeypatch, tmp_path, {"QUORUS_API_KEY": "mct_env_wins"})
    assert srv.API_KEY == "mct_env_wins"


def test_mcp_raises_clear_error_when_neither_source_has_valid_key(monkeypatch, tmp_path):
    _seed(tmp_path, "")
    with pytest.raises(SystemExit) as exc:
        _reload(monkeypatch, tmp_path, {"QUORUS_API_KEY": ""})
    assert "non-empty" in str(exc.value).lower()


def test_mcp_rejects_api_key_with_illegal_header_chars(monkeypatch, tmp_path):
    """A token containing CR/LF must fail closed, not ship as a Bearer header."""
    _seed(tmp_path, "mct_profile_value")
    with pytest.raises(SystemExit) as exc:
        _reload(monkeypatch, tmp_path, {"QUORUS_API_KEY": "mct_bad\nvalue"})
    assert "illegal" in str(exc.value).lower()


def test_mcp_warns_when_api_key_shape_is_unexpected(monkeypatch, tmp_path, caplog):
    """Non-mct shaped keys are accepted (test fixtures use them) but logged."""
    import logging

    _seed(tmp_path, "")
    with caplog.at_level(logging.WARNING, logger="mcp_tunnel.mcp"):
        srv = _reload(monkeypatch, tmp_path, {"QUORUS_API_KEY": "not_mct_shape"})
    assert srv.API_KEY == "not_mct_shape"
    assert any("mct_<hex>_<hex>" in r.message for r in caplog.records)


def test_mcp_accepts_production_shaped_api_key_silently(monkeypatch, tmp_path, caplog):
    """Production-shaped key (mct_<hex>_<hex>) must NOT trigger the advisory."""
    import logging

    _seed(tmp_path, "")
    with caplog.at_level(logging.WARNING, logger="mcp_tunnel.mcp"):
        srv = _reload(
            monkeypatch, tmp_path,
            {"QUORUS_API_KEY": "mct_abc123def456_0123456789abcdef0123456789abcdef"},
        )
    assert srv.API_KEY.startswith("mct_")
    assert not any("mct_<hex>_<hex>" in r.message for r in caplog.records)
