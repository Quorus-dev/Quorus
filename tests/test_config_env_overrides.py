"""A stray RELAY_URL in the shell silently beat the config file (2026-10-17)."""

from __future__ import annotations

import json

from quorus.config import env_overrides, load_config


def test_differing_env_vars_are_named_and_values_never_returned(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(json.dumps({
        "relay_url": "http://lan:8080", "relay_secret": "file-secret", "instance_name": "bob"}))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    for var in ("RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME"):
        monkeypatch.delenv(var, raising=False)
    assert env_overrides() == []

    monkeypatch.setenv("RELAY_URL", "http://dogfood:8787")
    monkeypatch.setenv("RELAY_SECRET", "env-secret")
    monkeypatch.setenv("INSTANCE_NAME", "bob")  # same value: not an override
    found = env_overrides()
    assert found == ["RELAY_URL", "RELAY_SECRET"]
    assert "env-secret" not in repr(found)
    assert load_config()["relay_url"] == "http://dogfood:8787"  # precedence unchanged
