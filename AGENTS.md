# AGENTS.md

This file is for agents working **on** the Quorus codebase. To **use** Quorus as an
agent (install it, join a room over MCP, call the tools), read `llms.txt` at the repo
root; it also ships in the package and is served over MCP as `quorus://llms.txt`.

Read CLAUDE.md: it holds the project rules, test and lint commands for every agent.

The essentials, for tools that don't read CLAUDE.md:

- Python lives in `~/dev/Quorus/.venv`. Don't look for another one, and never
  search the whole disk (`find /`) to find it.
- Tests, from an agent worktree:
  `PYTHONPATH=.:packages/cli:packages/mcp:packages/sdk:packages/tui ~/dev/Quorus/.venv/bin/python -m pytest -q -x <test files>`
- Run only the test files you touched; the laptop is shared, so no full-suite
  runs unless you're asked to.
- Lint: `~/dev/Quorus/.venv/bin/ruff check .`
