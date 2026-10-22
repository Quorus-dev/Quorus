"""Test and build leftovers that are never work, even in a repo with no .gitignore.

One list for both sides of a handoff turn: the daemon's commit step skips these
(a chat-only wake once committed __pycache__ to a user's main), and the handoff
clean-tree checks ignore them when untracked. Before they shared it, a receiver's
own pytest run left __pycache__ behind, nothing committed it, and completion
refused the "dirty" tree on every retry (Claude on 547f019b).
"""

from __future__ import annotations

GLOBS = (
    "**/__pycache__/**", "**/*.py[co]", "**/.pytest_cache/**", "**/.mypy_cache/**",
    "**/.ruff_cache/**", "**/node_modules/**", "**/.DS_Store", "**/.coverage",
    "**/*.egg-info/**", "**/.venv/**", "**/.tox/**", "graphify-out/**",
)

# git pathspecs that leave them out; pass after a positive pathspec such as "."
EXCLUDES = tuple(f":(exclude,glob){g}" for g in GLOBS)
