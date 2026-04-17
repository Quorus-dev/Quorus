"""Unit tests for ProfileManager (multi-workspace storage)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quorus.profiles import ProfileManager


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Give each test a clean ~/.quorus/ equivalent."""
    return tmp_path


def test_list_is_empty_on_fresh_dir(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.list() == []
    assert pm.current() is None
    assert pm.current_profile() is None


def test_save_creates_profile_file(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://example.test", "api_key": "sk_x"})

    path = tmp_config_dir / "profiles" / "work.json"
    assert path.exists()
    # 0o600 perms (owner rw only)
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = json.loads(path.read_text())
    assert loaded["relay_url"] == "https://example.test"


def test_list_returns_sorted_slugs(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("zeta", {"relay_url": "x"})
    pm.save("alpha", {"relay_url": "x"})
    assert pm.list() == ["alpha", "zeta"]


def test_set_current_and_current_profile(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u1", "api_key": "k1"})
    pm.save("personal", {"relay_url": "u2", "api_key": "k2"})
    pm.set_current("personal")
    assert pm.current() == "personal"
    data = pm.current_profile()
    assert data is not None
    assert data["api_key"] == "k2"


def test_set_current_rejects_missing_profile(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    with pytest.raises(FileNotFoundError):
        pm.set_current("ghost")


def test_get_returns_none_for_missing(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.get("nobody") is None


def test_get_returns_none_on_corrupt_file(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    (tmp_config_dir / "profiles").mkdir(parents=True)
    (tmp_config_dir / "profiles" / "broken.json").write_text("{ not json")
    assert pm.get("broken") is None


def test_delete_removes_file_and_unsets_current(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})
    pm.set_current("work")
    pm.delete("work")
    assert pm.list() == []
    assert pm.current() is None
    assert not (tmp_config_dir / "profiles" / "work.json").exists()


def test_delete_missing_profile_is_noop(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.delete("never-existed")  # should not raise


def test_invalid_slug_rejected(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    for bad in ["UPPER", "has space", "dot.slug", "", "-leading", "with/slash"]:
        with pytest.raises(ValueError):
            pm.save(bad, {"relay_url": "x"})


def test_valid_slugs_accepted(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    for good in ["work", "acme-corp", "a", "with_under", "team2"]:
        pm.save(good, {"relay_url": "x"})
    assert set(pm.list()) == {"work", "acme-corp", "a", "with_under", "team2"}


def test_migrate_legacy_flat_config(tmp_config_dir: Path) -> None:
    # Old shape: relay_url/api_key at top level, no "current"/"profiles".
    legacy = {
        "relay_url": "https://legacy.test",
        "instance_name": "aarya",
        "api_key": "sk_old",
    }
    (tmp_config_dir / "config.json").write_text(json.dumps(legacy))

    pm = ProfileManager(config_dir=tmp_config_dir)
    slug = pm.migrate_legacy_if_needed()

    assert slug == "default"
    assert pm.current() == "default"
    data = pm.current_profile()
    assert data is not None
    assert data["relay_url"] == "https://legacy.test"
    assert data["api_key"] == "sk_old"
    # Pointer file is now clean (no relay_url at top level).
    pointer = json.loads((tmp_config_dir / "config.json").read_text())
    assert "relay_url" not in pointer
    assert pointer["current"] == "default"


def test_migrate_noop_on_pointer_shape(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})
    pm.set_current("work")
    # Running migration again should do nothing.
    assert pm.migrate_legacy_if_needed() is None
    assert pm.current() == "work"


def test_migrate_noop_on_missing_config(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.migrate_legacy_if_needed() is None


def test_migrate_noop_on_corrupt_legacy(tmp_config_dir: Path) -> None:
    (tmp_config_dir / "config.json").write_text("{not valid json")
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.migrate_legacy_if_needed() is None
