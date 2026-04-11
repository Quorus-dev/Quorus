import json

import pytest

from tunnel_config import as_bool, load_config, resolve_config_file


def test_load_config_applies_env_overrides(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({
        "relay_url": "https://file.example",
        "relay_secret": "file-secret",
        "instance_name": "file-instance",
    }))

    monkeypatch.setenv("MCP_TUNNEL_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("RELAY_URL", "https://env.example")
    monkeypatch.setenv("RELAY_SECRET", "env-secret")
    monkeypatch.setenv("INSTANCE_NAME", "env-instance")

    config = load_config()

    assert config["relay_url"] == "https://env.example"
    assert config["relay_secret"] == "env-secret"
    assert config["instance_name"] == "env-instance"


def test_load_config_missing_file_uses_defaults(tmp_path, monkeypatch):
    """When no config file exists, defaults should be returned."""
    monkeypatch.setenv("MCP_TUNNEL_CONFIG_DIR", str(tmp_path / "nonexistent"))
    monkeypatch.delenv("RELAY_URL", raising=False)
    monkeypatch.delenv("RELAY_SECRET", raising=False)
    monkeypatch.delenv("INSTANCE_NAME", raising=False)

    config = load_config()

    assert config["relay_url"] == "http://localhost:8080"
    assert config["relay_secret"] == ""
    assert config["instance_name"] == "default"


def test_resolve_config_file_legacy_fallback(tmp_path, monkeypatch):
    """Should fall back to legacy dir when default doesn't exist."""
    monkeypatch.delenv("MCP_TUNNEL_CONFIG_DIR", raising=False)
    monkeypatch.setattr("tunnel_config.DEFAULT_CONFIG_DIR", tmp_path / "mcp-tunnel")
    monkeypatch.setattr("tunnel_config.LEGACY_CONFIG_DIR", tmp_path / "claude-tunnel")

    legacy_dir = tmp_path / "claude-tunnel"
    legacy_dir.mkdir()
    (legacy_dir / "config.json").write_text("{}")

    result = resolve_config_file()
    assert result == legacy_dir / "config.json"


def test_resolve_config_file_prefers_default_over_legacy(tmp_path, monkeypatch):
    """Default dir should take priority when both exist."""
    monkeypatch.delenv("MCP_TUNNEL_CONFIG_DIR", raising=False)
    monkeypatch.setattr("tunnel_config.DEFAULT_CONFIG_DIR", tmp_path / "mcp-tunnel")
    monkeypatch.setattr("tunnel_config.LEGACY_CONFIG_DIR", tmp_path / "claude-tunnel")

    for name in ("mcp-tunnel", "claude-tunnel"):
        d = tmp_path / name
        d.mkdir()
        (d / "config.json").write_text("{}")

    result = resolve_config_file()
    assert result == tmp_path / "mcp-tunnel" / "config.json"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("1", True),
        ("false", False),
        ("no", False),
        ("0", False),
        ("off", False),
        ("", False),
        ("  true  ", True),
    ],
)
def test_as_bool(value, expected):
    assert as_bool(value) is expected


def test_as_bool_default():
    assert as_bool(None, default=True) is True
    assert as_bool(None, default=False) is False


def test_load_config_corrupt_file_uses_defaults(tmp_path, monkeypatch):
    """Corrupt JSON should be silently ignored, returning defaults."""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("not json at all")

    monkeypatch.setenv("MCP_TUNNEL_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("RELAY_URL", raising=False)
    monkeypatch.delenv("RELAY_SECRET", raising=False)
    monkeypatch.delenv("INSTANCE_NAME", raising=False)

    config = load_config()

    assert config["relay_url"] == "http://localhost:8080"
    assert config["instance_name"] == "default"
