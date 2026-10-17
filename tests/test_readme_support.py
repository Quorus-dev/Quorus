"""The release README must distinguish tested wakes from experimental clients."""

from pathlib import Path


def test_readme_states_tested_auto_wake_scope():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "Auto-wake is tested with **Claude Code, Codex, Gemini CLI and opencode**" in readme
    # Clients without a validated wake are named as MCP-only, never implied to wake.
    assert "Cursor, Windsurf and Cline connect through MCP only" in readme
    assert "Zero config." not in readme
    assert "GET  /messages/{name}       # receive (SSE)" not in readme
