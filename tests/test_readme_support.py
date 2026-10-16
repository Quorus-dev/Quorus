"""The release README must distinguish tested wakes from experimental clients."""

from pathlib import Path


def test_readme_states_tested_auto_wake_scope():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "Auto-wake has been tested with **Claude Code and Codex**" in readme
    assert "experimental" in readme.lower()
    assert "Zero config." not in readme
    assert "GET  /messages/{name}       # receive (SSE)" not in readme
