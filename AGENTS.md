# AGENTS.md

Read CLAUDE.md: it holds the project rules, test and lint commands for every agent.

The essentials, for tools that don't read CLAUDE.md:

- Python lives in the repo's `.venv`; in an agent worktree, the main checkout's:
  `$(git rev-parse --git-common-dir)/../.venv`. Don't look for another one, and never search the whole disk
  (`find /`) to find it.
- Tests, from an agent worktree:
  `PYTHONPATH=.:packages/cli:packages/mcp:packages/sdk:packages/tui $(git rev-parse --git-common-dir)/../.venv/bin/python -m pytest -q -x <test files>`
- Run only the test files you touched; the machine may be shared, so no
  full-suite runs unless you're asked to.
- Lint: `$(git rev-parse --git-common-dir)/../.venv/bin/ruff check .`
