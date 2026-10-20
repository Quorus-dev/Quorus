"""Every test sees a temp room-bindings file (tests/conftest.py), never the real
~/.quorus one. PR #15's CI failed on all four Pythons because one test passed only
on a machine whose real bindings had a "build" room (2026-10-20), and Codex then
found `replay_cmd.bound_repo()` reading the real file past the first fixture.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

from quorus_cli import agent_cmd, replay_cmd

from quorus.routes import room_scorecard
from quorus.runtime import bindings

ROOT = Path(__file__).resolve().parents[1]


def test_the_bindings_path_is_never_the_real_home_during_tests(tmp_path):
    path = bindings.bindings_path()
    real = Path(os.path.expanduser("~")) / ".quorus" / "room-bindings.json"
    assert path != real and str(tmp_path) in str(path)
    assert agent_cmd.bindings_path() == path
    assert agent_cmd._bound_path("build") is None and not agent_cmd._room_bound("build")


def test_a_test_can_still_bind_a_room_in_its_own_file(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)  # the scorecard reader wants a repo, not a dir
    bindings.bindings_path().write_text(json.dumps({"build": {"path": str(repo)}}))
    assert agent_cmd._bound_path("build") == repo and agent_cmd._room_bound("build")
    assert replay_cmd.bound_repo("build") == repo
    assert room_scorecard.bound_repo("build") == repo


def test_direct_readers_never_see_a_sentinel_in_the_baseline_home(tmp_path, monkeypatch):
    """Codex's repro: a sentinel binding planted where an un-isolated reader would
    look. `Path.home()` is faked to a second dir that holds the sentinel; the fixture
    follows a faked HOME, so readers see that dir's file, and nothing ever reaches
    the process's baseline HOME (the real one on a developer's machine)."""
    sentinel_home = tmp_path / "sentinel-home"
    (sentinel_home / ".quorus").mkdir(parents=True)
    (tmp_path / ".git").mkdir()  # so every reader, the scorecard's included, accepts it
    (sentinel_home / ".quorus" / "room-bindings.json").write_text(
        json.dumps({"build": {"path": str(tmp_path)}}))
    monkeypatch.setattr("pathlib.Path.home", lambda: sentinel_home)
    # Readers follow the faked HOME, consistently, through the one resolver.
    assert bindings.bindings_path() == sentinel_home / ".quorus" / "room-bindings.json"
    assert replay_cmd.bound_repo("build") == tmp_path
    assert room_scorecard.bound_repo("build") == tmp_path
    assert agent_cmd._bound_path("build") == tmp_path


def test_no_module_spells_the_bindings_file_name_on_its_own():
    """The invariant behind the fixture: only the resolver, the daemon's constant and
    the demo's write into its own temp HOME may build the path. A new direct reader
    (`Path.home() / ".quorus" / "room-bindings.json"`) fails here before it can ever
    read a developer's real bindings. Prose that merely mentions the file (help text,
    docstrings) is fine: only a string constant equal to the file name is a path part."""
    allowed = {
        "quorus/runtime/bindings.py",          # the resolver
        "scripts/reflexd.py",                   # ROOM_BINDINGS_PATH, isolated in conftest
        "packages/cli/quorus_cli/demo_cmd.py",  # writes into the demo's own temp HOME
    }
    offenders = []
    for path in list((ROOT / "quorus").rglob("*.py")) + list((ROOT / "packages").rglob("*.py")) \
            + list((ROOT / "scripts").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if rel in allowed or "/tests/" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "room-bindings.json":
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"spell the path through quorus.runtime.bindings: {offenders}"


def test_the_whole_session_runs_in_a_throwaway_home():
    """Overseer, 2026-10-20: instead of guarding each read API (open, Path.read_*,
    bytes paths, "+" modes, os.open; mmap and `cat` next), tests/conftest.py moves
    HOME off the real one before any quorus import. A dynamically built path, a
    subprocess and the product's own resolver all land in the throwaway HOME."""
    import subprocess
    import tempfile

    real_home = Path(os.environ["QUORUS_TESTS_REAL_HOME"])
    home = Path.home()
    assert home != real_home and real_home not in home.parents
    assert Path(tempfile.gettempdir()).resolve() in home.resolve().parents

    sneaky = Path(os.path.expanduser("~")) / ".quorus" / ("room-" + "bindings.json")
    assert real_home not in sneaky.parents
    assert real_home not in bindings.bindings_path().parents

    child = subprocess.run(["sh", "-c", 'printf %s "$HOME"'], capture_output=True, text=True)
    assert Path(child.stdout) == home, child
