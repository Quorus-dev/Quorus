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

| Module               | Lines | What                                                                                                                                                                                   |
| -------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| murmur/relay.py      | ~900  | FastAPI relay: rooms, fan-out, SSE, history, presence, rate limiting, peek, web dashboard (building), invite pages (building)                                                          |
| murmur/mcp.py        | ~600  | MCP server: 8 tools, SSE listener, auto-poll, heartbeat, lazy poll mode                                                                                                                |
| murmur/cli.py        | ~550  | 20 CLI commands: init, relay, rooms, create, invite, join, say, dm, watch, chat, history, ps, status, invite-link, spawn, spawn-multiple, quickstart, watch-daemon, hackathon, members |
| murmur/config.py     | ~80   | Config loading (env > file > defaults), poll mode support                                                                                                                              |
| murmur/analytics.py  | ~90   | Terminal dashboard                                                                                                                                                                     |
| murmur/integrations/ | ~200  | Universal HTTP client (Python + TypeScript)                                                                                                                                            |
| tests/               | ~1400 | 134 tests: relay, mcp, config, integration, rooms, stress, hackathon readiness                                                                                                         |

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich, hatchling

**20 CLI commands available via `murmur <command>`**

**Key features:**

- Rooms with fan-out messaging (send once, all members receive)
- SSE push delivery (real-time, replaces polling)
- Message types: chat, claim, status, request, alert, sync
- Room history (persistent, not cleared on read)
- Agent presence/heartbeat system
- Per-sender rate limiting
- Universal HTTP API (any agent platform can connect)
- Python + TypeScript client libraries
- murmur spawn/spawn-multiple (auto-launch agents)
- murmur quickstart (one-command demo)
- murmur hackathon (two-room hackathon setup)
- Docker + docker-compose + Railway/Render deploy configs
- Peek endpoint for non-destructive inbox checks
- Watcher daemon for file-based notifications
- Interactive chat mode (murmur chat)
- OpenAPI docs at /docs
- Web dashboard at GET / (live messages, room list, send from browser)
- Discord-style invite pages at GET /invite/{room} (join from browser, no install)
- Integration guides for Codex, Cursor, Gemini, Ollama
- Launch content (Show HN draft, X/Twitter thread draft)

**Tests:** 134 passing, all green. Stress tested: 281 msg/s, p50=3.6ms. 6 agents across 2 isolated rooms verified.

---

## In Progress (agents building now)

- agent-3: Adding test coverage for presence/heartbeat and peek endpoints

---

## Recent Changes

| Date       | Commit  | What                                               |
| ---------- | ------- | -------------------------------------------------- |
| 2026-04-11 | c02144d | Show HN + Twitter launch drafts                    |
| 2026-04-11 | 02d4822 | Integration guides (Codex, Cursor, Gemini, Ollama) |
| 2026-04-11 | ed32c0b | Web dashboard at GET / with live SSE messages      |
| 2026-04-11 | d86f531 | Discord-style invite pages at /invite/{room}       |
| 2026-04-11 | ae09ffa | murmur hackathon command                           |
| 2026-04-11 | 65b1d58 | Peek endpoint for non-destructive inbox check      |
| 2026-04-11 | 9bb599b | TypeScript MurmurClient + peek in Python client    |
| 2026-04-11 | 6551487 | Hackathon readiness test — 6 agents, 2 rooms       |
| 2026-04-11 | 5584939 | Stress test — 281 msg/s, p50=3.6ms                 |
| 2026-04-11 | 6477dc2 | Lazy poll mode + OpenAPI docs                      |

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

## Hackathon Setup (April 15)

```bash
murmur hackathon --yc-agents 3 --oai-agents 3
```

Creates yc-hack + openai-hack rooms, spawns agents, gives missions.

---

## Key Decisions

- Package name: murmur-ai (murmur was taken on PyPI)
- Private repo until launch — distribute via git+https
- Free stack only — no paid dependencies
- MIT licensed
- Relay is the universal API — MCP is one client integration
- Web dashboard for non-technical users (building)
- Medium effort for implementation, high/max for architecture

---

## Contributors

- **Arav** (aravkek) — co-founder, parent Claude directing agents
- **Aarya** (Aarya2004) — co-founder, joining with agents soon
