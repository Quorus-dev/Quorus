"""The release README must distinguish tested wakes from experimental clients."""

from pathlib import Path


def test_readme_states_tested_auto_wake_scope():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "Auto-wake is tested with **Claude Code, Codex and opencode**" in readme
    # Untested wakes are named as such, never implied to work.
    assert "Gemini CLI auto-wake is built but not yet validated live" in readme
    assert "Zero config." not in readme
    assert "GET  /messages/{name}       # receive (SSE)" not in readme
