import json

from tunnel_config import load_config


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
