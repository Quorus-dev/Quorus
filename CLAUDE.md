# CLAUDE.md — Murmur

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

Murmur (mcp-tunnel) — relay-based inter-agent communication for distributed Claude Code instances.

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich

**Run tests:** `pytest -v`
**Lint:** `ruff check .`
**Lint fix:** `ruff check . --fix`

---

## Architecture

```
Claude Code A -> MCP Server (stdio) -> HTTP -> [Relay Server (FastAPI)] -> HTTP -> MCP Server (stdio) -> Claude Code B
```

- `relay_server.py` — Central relay (FastAPI). Per-recipient queues, persistence, chunking, analytics.
- `mcp_server.py` — Local MCP server. Tools: send_message, check_messages, list_participants.
- `tunnel_config.py` — Config loading. Priority: env vars > ~/mcp-tunnel/config.json > legacy fallback.
- `analytics.py` — CLI dashboard for relay stats.

---

## Rules

- Files under 500 lines. Split if larger.
- Async-first. Use `asyncio.to_thread` for blocking I/O.
- All external input validated before use.
- Never log secrets or tokens.
- Tests required for new features and bug fixes.
- Conventional commits, under 50 chars, imperative mood.
