"""Tests for config path resolution priority."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quorus import config as config_mod
from quorus.config import (
    CONFIG_FILENAME,
    ConfigManager,
    resolve_config_dir,
    resolve_config_file,
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point $HOME at a tmp dir and clear the override env vars so each test
    starts from a clean state. Also resets the legacy-warning dedupe set."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("QUORUS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MCP_TUNNEL_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_DIR", tmp_path / ".quorus")
    monkeypatch.setattr(config_mod, "LEGACY_CONFIG_DIR", tmp_path / "mcp-tunnel")
    config_mod._warned_paths.clear()
    yield tmp_path


def _seed(dir_path: Path, payload: dict | None = None) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    file = dir_path / CONFIG_FILENAME
    file.write_text(json.dumps(payload or {"seed": True}))
    return file


def test_quorus_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "custom"))
    assert resolve_config_dir() == tmp_path / "custom"
    assert resolve_config_file() == tmp_path / "custom" / CONFIG_FILENAME


def test_mcp_tunnel_env_wins_over_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TUNNEL_CONFIG_DIR", str(tmp_path / "alt"))
    _seed(tmp_path / ".quorus")  # would otherwise win priority 3
    assert resolve_config_dir() == tmp_path / "alt"


def test_modern_home_dir_wins_when_present(tmp_path):
    _seed(tmp_path / ".quorus")
    _seed(tmp_path / ".murmur")  # should be ignored
    assert resolve_config_dir() == tmp_path / ".quorus"


def test_murmur_fallback_when_no_modern(tmp_path, caplog):
    _seed(tmp_path / ".murmur", {"legacy": "murmur"})
    with caplog.at_level("WARNING"):
        resolved = resolve_config_dir()
    assert resolved == tmp_path / ".murmur"
    assert any("deprecated" in rec.message for rec in caplog.records)


def test_mcp_tunnel_fallback_when_no_modern(tmp_path, caplog):
    _seed(tmp_path / "mcp-tunnel", {"legacy": "mcp-tunnel"})
    with caplog.at_level("WARNING"):
        resolved = resolve_config_dir()
    assert resolved == tmp_path / "mcp-tunnel"
    assert any("deprecated" in rec.message for rec in caplog.records)


def test_save_refuses_legacy_murmur(tmp_path):
    legacy = tmp_path / ".murmur"
    legacy.mkdir()
    mgr = ConfigManager(legacy / CONFIG_FILENAME)
    with pytest.raises(ValueError, match="legacy"):
        mgr.save({"a": 1})


def test_save_refuses_legacy_mcp_tunnel(tmp_path):
    legacy = tmp_path / "mcp-tunnel"
    legacy.mkdir()
    mgr = ConfigManager(legacy / CONFIG_FILENAME)
    with pytest.raises(ValueError, match="legacy"):
        mgr.save({"a": 1})


def test_save_lands_on_modern_dir_even_with_legacy_present(tmp_path):
    """Regression: when only legacy dirs exist, save() still targets modern."""
    _seed(tmp_path / ".murmur")
    # resolve_config_dir will return the legacy (because modern doesn't exist),
    # but explicit save via modern path should just work.
    modern_file = tmp_path / ".quorus" / CONFIG_FILENAME
    ConfigManager(modern_file).save({"hello": "world"})
    assert modern_file.exists()
    assert json.loads(modern_file.read_text()) == {"hello": "world"}
    # Legacy file untouched.
    assert json.loads((tmp_path / ".murmur" / CONFIG_FILENAME).read_text()) == {"seed": True}


def test_legacy_warning_once_per_process(tmp_path, caplog):
    _seed(tmp_path / ".murmur")
    with caplog.at_level("WARNING"):
        resolve_config_dir()
        resolve_config_dir()
        resolve_config_dir()
    msgs = [r for r in caplog.records if "deprecated" in r.message]
    assert len(msgs) == 1, "deprecation should warn once per process per path"
