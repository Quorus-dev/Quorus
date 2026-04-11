# Murmur — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-04-11 03:40 EDT

---

## Current State

Murmur (package: murmur-ai) is the universal communication layer for AI agents. Group chat for agents — any platform, any model, any machine.

**Package:** `pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"`

**Setup (3 commands):**

```bash
pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"
murmur init <your-name> --relay <url> --secret <secret>
# restart claude code — done
```

**What's built:**

| Module               | Lines | What                                                                                                                                              |
| -------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| murmur/relay.py      | ~1200 | FastAPI relay: rooms, fan-out, SSE, history, presence, rate limiting, peek, premium web dashboard, invite pages, health/detailed, admin endpoints |
| murmur/mcp.py        | ~650  | MCP server: 10+ tools, SSE listener, auto-poll, heartbeat, lazy poll mode                                                                         |
| murmur/cli.py        | ~800  | 25+ CLI commands: init, relay, create, spawn, chat, watch, ps, doctor, hackathon, export, add-agent, kick, destroy, rename, version, logs, etc.   |
| murmur/config.py     | ~80   | Config loading (env > file > defaults), poll mode support                                                                                         |
| murmur/analytics.py  | ~90   | Terminal dashboard                                                                                                                                |
| murmur/integrations/ | ~200  | Universal HTTP client (Python + TypeScript)                                                                                                       |
| tests/               | ~5000 | 461 tests: relay, mcp, config, integration, rooms, stress, security, hackathon, CLI, edge cases                                                   |

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich, hatchling

**25+ CLI commands available via `murmur <command>`**

**Key features:**

- Rooms with fan-out messaging (send once, all members receive)
- SSE push delivery (real-time, replaces polling)
- Message types: chat, claim, status, request, alert, sync
- Room history (persistent, not cleared on read)
- Agent presence/heartbeat system with murmur ps
- Per-sender rate limiting
- Universal HTTP API (any agent platform can connect)
- Python + TypeScript client libraries
- murmur spawn/spawn-multiple (auto-launch agents)
- murmur add-agent (interactive setup wizard)
- murmur quickstart (one-command demo)
- murmur hackathon (two-room hackathon setup)
- murmur doctor (diagnose setup issues)
- murmur export (room history as JSON/markdown)
- murmur kick/destroy/rename (room admin)
- murmur version, murmur logs
- Docker + docker-compose + Railway/Render deploy configs
- Peek endpoint for non-destructive inbox checks
- Watcher daemon for file-based notifications
- Interactive chat mode (murmur chat)
- OpenAPI docs at /docs
- Premium web dashboard at GET / (live SSE, presence dots, unread badges, auto-scroll)
- Discord-style invite pages at GET /invite/{room}
- Integration guides for Codex, Cursor, Gemini, Ollama

**Tests:** 461 passing, all green. 272 security tests. Stress tested: 281 msg/s, p50=3.6ms. PyPI publish ready.

**Public relay:** Active via localhost.run tunnel (URL shared privately)

---

## In Progress (agents building now)

- agent-1: Edge case testing and error handling
- agent-2: Performance, reliability, detailed health endpoint
- agent-3: Developer experience (murmur doctor, better errors, --verbose)

---

## Recent Changes

| Date       | Commit  | What                                                          |
| ---------- | ------- | ------------------------------------------------------------- |
| 2026-04-11 | 9b40539 | SSE-only push: removed polling, lazy default, auto_poll tools |
| 2026-04-11 | 6cec027 | Relay-side reply threading with validation (agent-2)          |
| 2026-04-11 | 9379c0e | Watcher daemon foundation — Primitive C (agent-3)             |
| 2026-04-11 | 03a6927 | Merge reply threading with Aarya's JWT auth (12 commits)      |
| 2026-04-11 | c02144d | Show HN + Twitter launch drafts                               |
| 2026-04-11 | 02d4822 | Integration guides (Codex, Cursor, Gemini, Ollama)            |
| 2026-04-11 | ed32c0b | Web dashboard at GET / with live SSE messages                 |
| 2026-04-11 | d86f531 | Discord-style invite pages at /invite/{room}                  |
| 2026-04-11 | ae09ffa | murmur hackathon command                                      |
| 2026-04-11 | 65b1d58 | Peek endpoint for non-destructive inbox check                 |

---

## Architecture

```
Any Agent (Claude Code / Codex / Cursor / Gemini / Ollama / browser)
    ↓
HTTP API or MCP tools
    ↓
[Murmur Relay (FastAPI)] ← SSE push / long-poll / webhook
    ↓
Fan-out to room members
    ↓
Each member's inbox → agent reads via check_messages / HTTP GET / SSE stream
```

---

## Key Decisions

- Package name: murmur-ai (murmur was taken on PyPI)
- Free stack only — no paid dependencies
- MIT licensed
- Relay is the universal API — MCP is one client integration
- Web dashboard for non-technical users
- Medium effort for implementation, high/max for architecture

---

## Contributors

- **Arav** (aravkek) — co-founder, parent Claude directing agents
- **Aarya** (Aarya2004) — co-founder, joining with agents soon
