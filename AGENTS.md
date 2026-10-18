# AGENTS.md

Read CLAUDE.md: it holds the project rules, test and lint commands for every agent.

The essentials, for tools that don't read CLAUDE.md:

- Python lives in `~/dev/Quorus/.venv`. Don't look for another one, and never
  search the whole disk (`find /`) to find it.
- Tests, from an agent worktree:
  `PYTHONPATH=.:packages/cli:packages/mcp:packages/sdk:packages/tui ~/dev/Quorus/.venv/bin/python -m pytest -q -x <test files>`
- Run only the test files you touched; the laptop is shared, so no full-suite
  runs unless you're asked to.
- Lint: `~/dev/Quorus/.venv/bin/ruff check .`
