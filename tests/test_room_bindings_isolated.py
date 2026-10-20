"""Every test sees a temp room-bindings file (tests/conftest.py), never the real
~/.quorus one. PR #15's CI failed on all four Pythons because one test passed only
on a machine whose real bindings had a "build" room (2026-10-20)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from quorus_cli import agent_cmd


def test_the_bindings_path_is_never_the_real_home_during_tests(tmp_path):
    path = agent_cmd.bindings_path()
    real = Path(os.path.expanduser("~")) / ".quorus" / "room-bindings.json"
    assert path != real and str(tmp_path) in str(path)
    assert agent_cmd._bound_path("build") is None and not agent_cmd._room_bound("build")


def test_a_test_can_still_bind_a_room_in_its_own_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    agent_cmd.bindings_path().write_text(json.dumps({"build": {"path": str(repo)}}))
    assert agent_cmd._bound_path("build") == repo and agent_cmd._room_bound("build")
