"""The relay's key store must never live relative to the working directory."""

from __future__ import annotations

import os
from pathlib import Path

from quorus.auth import participant_keys


def test_data_dir_ignores_the_working_directory(tmp_path: Path, monkeypatch) -> None:
    for var in ("QUORUS_HISTORY_DB", "MURMUR_HISTORY_DB", "MESSAGES_FILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir("/")  # what launchd gives a relay
    monkeypatch.setenv("HOME", str(tmp_path))
    assert participant_keys.data_dir() == tmp_path / ".quorus"
    monkeypatch.setenv("MESSAGES_FILE", str(tmp_path / "dogfood" / "state.json"))
    assert participant_keys.data_dir() == (tmp_path / "dogfood").resolve()
    monkeypatch.setenv("QUORUS_HISTORY_DB", str(tmp_path / "db" / "history.db"))
    assert participant_keys.data_dir() == (tmp_path / "db").resolve()
    assert not str(participant_keys.data_dir()).startswith(os.sep + ".quorus")
