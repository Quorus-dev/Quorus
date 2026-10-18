"""CI refuses dev notes on public branches (0.5.1, after 76a56aea moved them out).

Checked in the workflow rather than here: weekend/* branches keep docs/internal
on purpose, and CI only runs for main, dev and PRs to them.
"""

from pathlib import Path

_CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def test_ci_fails_when_dev_notes_are_tracked() -> None:
    ci = _CI.read_text()
    assert "branches: [main, dev]" in ci  # weekend/* never runs it
    job = ci.split("\n  public-tree:\n", 1)[1].split("\n  test:\n", 1)[0]
    assert ("git ls-files docs/internal docs/weekend docs/superpowers graphify-out"
            in job)
    assert "exit 1" in job
