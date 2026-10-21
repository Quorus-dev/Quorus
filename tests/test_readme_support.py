"""The release README must distinguish tested wakes from experimental clients."""

from pathlib import Path


def test_readme_states_tested_auto_wake_scope():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "Auto-wake is tested with **Claude Code, Codex and opencode**" in readme
    # Gemini CLI is a note (AI Studio API key), not a tested wake.
    assert "Gemini CLI wakes with a Google AI Studio API key" in readme
    # Clients without a validated wake are named as MCP-only, never implied to wake.
    assert "Cursor, Windsurf and Cline connect through MCP only" in readme
    assert "Zero config." not in readme
    assert "GET  /messages/{name}       # receive (SSE)" not in readme


def test_readme_says_invites_no_longer_carry_the_secret():
    """0.6.0: an invite redeems into the joiner's own key (test_invite_codes.py)."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "*is* the\n  secret" not in readme and "is the secret" not in readme
    assert "**Invites don't hand out the secret.**" in readme
    assert "quorus invites --revoke <id>" in readme


def test_readme_local_links_exist():
    """README pointed at /docs/deployment.md, which didn't exist (PyPI page too)."""
    import re

    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text()
    ours = r"https://(?:github\.com/Quorus-dev/Quorus/(?:blob|tree)/main|"
    ours += r"raw\.githubusercontent\.com/Quorus-dev/Quorus/main)/([^)\s#]+)"
    paths = re.findall(ours, readme)
    paths += [p for p in re.findall(r"\]\(([^)\s#]+)", readme) if "://" not in p
              and not p.startswith("mailto:")]
    paths += re.findall(r"`/?((?:docs|website|scripts)/[^`\s]+)`", readme)
    assert paths, "no local links found; the pattern is broken"
    missing = sorted({p for p in paths if not (root / p.lstrip("/")).exists()})
    assert missing == []
