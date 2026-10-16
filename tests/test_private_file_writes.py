"""Credential writers must not follow symlinks or reuse public temp files."""

import json

import pytest
from quorus_cli import agent_cmd

from quorus.config import ConfigManager
from quorus.profiles import _atomic_write_json
from scripts import reflexd

wake = reflexd.reflexd_wake


def _write(kind, path):
    if kind == "claude":
        wake.write_claude_mcp_config({"env": {"QUORUS_API_KEY": "secret"}}, path)
    elif kind == "wake":
        wake._write_private_json(path, {"api_key": "secret"})
    elif kind == "agent":
        agent_cmd._write_private(path, json.dumps({"api_key": "secret"}))
    elif kind == "config":
        ConfigManager(path).save({"api_key": "secret"})
    else:
        _atomic_write_json(path, {"api_key": "secret"})


@pytest.mark.parametrize("kind", ["claude", "wake", "agent", "config", "profile"])
@pytest.mark.parametrize("symlink", [False, True])
def test_private_writes_replace_unsafe_files(tmp_path, kind, symlink):
    root = tmp_path / "config"
    root.mkdir()
    path = root / "credentials.json"
    # The config/profile writers used a predictable .tmp path; the other
    # writers truncated the destination directly. Exercise the actual target.
    unsafe = path.with_suffix(".json.tmp") if kind in {"config", "profile"} else path
    outside = tmp_path / "unrelated-file"
    outside.write_text("keep me")
    if symlink:
        unsafe.symlink_to(outside)
    else:
        unsafe.write_text("old contents")
        unsafe.chmod(0o644)
    _write(kind, path)
    assert outside.read_text() == "keep me", "write followed a symlink outside config"
    assert not path.is_symlink()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "secret" in path.read_text()
