# CLAUDE.md — Quorus

## Shared Context Protocol

**Read `CONTEXT.md` at the start of every session.** It contains the current project state, what's in progress, recent changes, architectural decisions, and next priorities. It is the shared memory between all contributors' Claude instances.

**Update `CONTEXT.md` after every significant change:**

- Add your changes to "Recent Changes" (keep last 10, drop oldest)
- Update "In Progress" when starting/finishing work
- Update "Current State" if the system's capabilities changed
- Add to "Decisions Made" when architectural choices are made
- Update "Next Up" when priorities shift

---

## Project

Quorus — coordination layer for AI agent swarms. Real-time rooms, messaging, distributed locks.

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich

**Run tests:** `pytest -v`
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
