# CLAUDE.md — Quorus

## Project

Quorus — coordination layer for AI agent swarms. Real-time rooms, messaging, distributed locks.

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich

**Run tests:** `pytest -v`
**Setup:** `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
**From an agent worktree** (no venv of its own), use the main checkout's: `PYTHONPATH=.:packages/cli:packages/mcp:packages/sdk:packages/tui $(git rev-parse --git-common-dir)/../.venv/bin/python -m pytest -q`
**Lint:** `ruff check .`
**Lint fix:** `ruff check . --fix`

---

## Architecture

```
Claude Code A -> MCP Server (stdio) -> HTTP -> [Quorus Relay (FastAPI)] -> HTTP -> MCP Server (stdio) -> Claude Code B
```

- `quorus/relay.py` — Central relay (FastAPI). Rooms, SSE fan-out, persistence, rate limiting.
- `quorus_mcp/server.py` — MCP server. 12 tools for coordination.
- `quorus/config.py` — Config loading. Priority: env vars > ~/.quorus/config.json > legacy fallback.
- `quorus_cli/cli.py` — CLI commands. `quorus` opens TUI by default.

---

## Rules

- Files under 500 lines. Split if larger.
- Async-first. Use `asyncio.to_thread` for blocking I/O.
- All external input validated before use.
- Never log secrets or tokens.
- Tests required for new features and bug fixes.
- Conventional commits, under 50 chars, imperative mood.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
