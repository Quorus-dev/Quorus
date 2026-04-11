# Murmur — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-04-11 22:00 EDT

---

## Current State

Murmur (package: murmur-ai) is the universal communication substrate for AI agent swarms. "VS Code Live Share for AI Agents" — any model, any machine, any platform coordinates in real-time.

**Branch:** `dev` (700 tests passing) — ready to merge to main before April 16 demo.

**Package:** `pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"`

**Setup (3 commands):**

```bash
pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"
murmur init <your-name> --relay <url> --secret <secret>
# restart claude code — done
```

**What's built:**

| Module                      | Lines  | What                                                                                     |
| --------------------------- | ------ | ---------------------------------------------------------------------------------------- |
| murmur/relay.py             | ~525   | FastAPI relay: rooms, SSE fan-out, history, presence, rate limiting, health, admin       |
| murmur/mcp.py               | ~820   | MCP server: 12 tools incl. claim_task, release_task, get_room_state, SSE push, heartbeat |
| murmur/cli.py               | ~2500  | 28+ CLI commands incl. state, locks, usage, init, relay, create, spawn, hackathon, etc.  |
| murmur/routes/room_state.py | ~250   | Primitive A+B: GET state, PATCH goal, POST decisions, POST/DELETE locks (mutex)          |
| murmur/routes/usage.py      | ~157   | GET /v1/usage + /v1/usage/rooms/{room} — tenant-scoped metrics                           |
| murmur/routes/agents.py     | ~57    | GET /agents/{name} — profile, rooms, last seen, message count, online status             |
| murmur/watcher.py           | ~238   | Primitive C: SSE-driven daemon, writes .murmur/context.md for IDE indexing               |
| murmur/dashboard.py         | ~large | Web dashboard: live messages + swarm activity panel + usage bar                          |
| murmur/backends/            | ~900   | In-memory + Redis backends for all state (incl. RoomStateBackend)                        |
| tests/                      | ~7000  | 700 tests: relay, mcp, config, CLI, usage, agents, room_state, watcher, stress, security |

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich, hatchling

**Key features (complete):**

- Rooms with fan-out messaging (send once, all members receive)
- **SSE-only push delivery** — zero polling, instant delivery
- **Primitive A: Shared State Matrix** — GET /rooms/{room}/state → goal, locked files, claimed tasks, decisions, active agents
- **Primitive B: Distributed Mutex Locking** — POST/DELETE /rooms/{room}/lock, SSE broadcast LOCK_ACQUIRED/LOCK_RELEASED, TTL auto-expire
- **Primitive C: Watcher Daemon** — writes .murmur/context.md for IDE indexing, event-driven via SSE
- **MCP tools**: claim_task, release_task, get_room_state (12 tools total)
- **Usage metrics**: GET /v1/usage — messages, active agents, per-room breakdown, top senders
- **Agent identity**: GET /agents/{name} — profile card, rooms, last seen, message count
- **CLI commands**: murmur state, murmur locks, murmur usage (+ 25 others)
- **Dashboard**: live swarm panel — active goal, locked files countdown, agent presence, usage bar
- JWT auth + API keys, per-sender rate limiting
- Docker + Railway/Render deploy configs
- Reply threading (reply_to field + Room.reply() SDK)
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
