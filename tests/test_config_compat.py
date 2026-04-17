"""ConfigManager stays backward-compatible when profiles exist."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quorus.config import ConfigManager
from quorus.profiles import ProfileManager


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_load_returns_current_profile_when_profiles_exist(
    tmp_config_dir: Path,
) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://work.test", "api_key": "sk_w"})
    pm.save("personal", {"relay_url": "https://personal.test", "api_key": "sk_p"})
    pm.set_current("work")

    data = ConfigManager().load()
    assert data["relay_url"] == "https://work.test"
    assert data["api_key"] == "sk_w"


def test_load_auto_migrates_legacy_flat_config(tmp_config_dir: Path) -> None:
    (tmp_config_dir / "config.json").write_text(
        json.dumps({"relay_url": "https://legacy.test", "api_key": "sk_old"})
    )

    data = ConfigManager().load()
    assert data["relay_url"] == "https://legacy.test"

    # After migration, there should be a profile file and a pointer.
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.current() == "default"
    assert (tmp_config_dir / "profiles" / "default.json").exists()


def test_load_returns_empty_dict_when_no_config(tmp_config_dir: Path) -> None:
    assert ConfigManager().load() == {}


def test_load_with_explicit_profile(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://work.test"})
    pm.save("personal", {"relay_url": "https://personal.test"})
    pm.set_current("work")

    # Asking for a specific profile bypasses "current".
    data = ConfigManager(profile="personal").load()
    assert data["relay_url"] == "https://personal.test"


def test_load_with_missing_explicit_profile_returns_empty(
    tmp_config_dir: Path,
) -> None:
    assert ConfigManager(profile="ghost").load() == {}
