"""Round-trip tests for ConfigManager."""
from __future__ import annotations

import json
import os
import stat

import pytest

from quorus.config import CONFIG_FILENAME, ConfigManager


def test_load_returns_empty_when_missing(tmp_path):
    mgr = ConfigManager(tmp_path / CONFIG_FILENAME)
    assert mgr.load() == {}


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / CONFIG_FILENAME
    mgr = ConfigManager(path)
    mgr.save({"relay_url": "http://x", "api_key": "k", "count": 3})
    assert mgr.load() == {"relay_url": "http://x", "api_key": "k", "count": 3}


def test_save_creates_parent_dir(tmp_path):
    nested = tmp_path / "a" / "b" / CONFIG_FILENAME
    ConfigManager(nested).save({"ok": True})
    assert nested.exists()


def test_save_uses_0600_perms(tmp_path):
    path = tmp_path / CONFIG_FILENAME
    ConfigManager(path).save({"secret": "val"})
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_load_returns_empty_on_bad_json(tmp_path):
    path = tmp_path / CONFIG_FILENAME
    path.write_text("{not json")
    assert ConfigManager(path).load() == {}


def test_save_atomic_no_partial_file_on_crash(tmp_path, monkeypatch):
    """If the write stream raises, the target path should not exist yet
    (the write goes through a .tmp file that is only renamed on success)."""
    path = tmp_path / CONFIG_FILENAME
    real_dump = json.dump

    def bad_dump(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(json, "dump", bad_dump)
    with pytest.raises(RuntimeError):
        ConfigManager(path).save({"a": 1})
    # Original target must not exist.
    assert not path.exists()
    # Restore so teardown is clean.
    monkeypatch.setattr(json, "dump", real_dump)
