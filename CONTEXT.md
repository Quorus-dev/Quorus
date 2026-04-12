# Murmur — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-04-12 11:30 EDT

---

## Current State

Murmur (package: murmur-ai) is the universal communication substrate for AI agent swarms. "VS Code Live Share for AI Agents" — any model, any machine, any platform coordinates in real-time.

**Branch:** `main` (722 tests passing) — dev merged to main on 2026-04-12.

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
| murmur/relay.py             | ~1200  | FastAPI relay: rooms, SSE fan-out, history, presence, rate limiting, health, admin       |
| murmur/mcp_server.py        | ~820   | MCP server: 12 tools incl. claim_task, release_task, get_room_state, SSE push, heartbeat |
| murmur/cli.py               | ~3500  | 30+ CLI commands incl. context (Summary Cascade v1), decision, state, locks, usage, etc. |
| murmur/routes/room_state.py | ~250   | Primitive A+B: GET state, PATCH goal, POST decisions, POST/DELETE locks (mutex)          |
| murmur/routes/usage.py      | ~157   | GET /v1/usage + /v1/usage/rooms/{room} — tenant-scoped metrics                           |
| murmur/routes/agents.py     | ~57    | GET /agents/{name} — profile, rooms, last seen, message count, online status             |
| murmur/watcher.py           | ~238   | Primitive C: SSE-driven daemon, writes .murmur/context.md for IDE indexing               |
| murmur/dashboard.py         | ~large | Web dashboard: live messages + swarm activity panel + usage bar                          |
| murmur/backends/            | ~900   | In-memory + Redis backends for all state (incl. RoomStateBackend)                        |
| tests/                      | ~7500  | 738 tests: relay, mcp, config, CLI, usage, agents, room_state, watcher, stress, security |

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
- Durable webhook queue (Redis Streams, ACK/NACK + DLQ)
- Idempotency-Key header support for sends
- ack=manual default — at-least-once delivery to caller
- murmur add-agent (interactive setup wizard)
- murmur quickstart (one-command demo)
- murmur hackathon (two-room hackathon setup)
- murmur doctor (diagnose setup issues)
- murmur export (room history as JSON/markdown)
- murmur kick/destroy/rename (room admin)
- OpenAPI docs at /docs
- Premium web dashboard at GET / (live SSE, presence dots, unread badges, auto-scroll)
- Discord-style invite pages at GET /invite/{room}
- Integration guides for Codex, Cursor, Gemini, Ollama
- **Auto-inject messages**: `murmur hook enable` + `murmur inbox` for UserPromptSubmit hook
- **MCP server instructions**: Agents receive guidance on tools and expected behavior
- **Summary Cascade v1**: `murmur context [--room R] [--quiet] [--json]` — injected briefing of active goal, briefs, claimed tasks, decisions, status updates, locked files; zero vector DB
- **Decision recording**: `murmur decision <room> "<text>"` — writes to room state decisions via POST /rooms/{room}/state/decisions; surfaced in `murmur context`
- **Hook auto-injection**: `murmur hook enable` now runs `murmur inbox --quiet && murmur context --quiet` on every UserPromptSubmit

**Tests:** 738 passing + 14 Redis integration tests. 272 security tests. Stress tested: 281 msg/s, p50=3.6ms.

**Public relay:** Active via localhost.run tunnel (URL shared privately)

---

## Production Readiness

**Current rating:** Private demo 9.0/10 | Public alpha 7.8/10 | Production SaaS 6.0/10

### Delivery Guarantees

| Path                     | Guarantee                   | Notes                                                           |
| ------------------------ | --------------------------- | --------------------------------------------------------------- |
| HTTP client + manual ACK | At-least-once to caller     | Caller must call `result.ack()` after processing                |
| HTTP client + auto ACK   | At-most-once                | Messages deleted before caller processes — **footgun**          |
| MCP tools                | At-least-once to MCP server | ACK happens after formatting, before tool result reaches Claude |
| SSE                      | Live notifications only     | Not durable — use for real-time UX, not delivery                |
| Webhooks (durable)       | At-least-once               | Redis Streams queue with ACK/NACK + DLQ                         |

### Blockers for Production SaaS

| Priority     | Issue                                        | Status                                                  |
| ------------ | -------------------------------------------- | ------------------------------------------------------- |
| ~~Critical~~ | ~~No real Redis/Postgres integration tests~~ | ✅ 14 integration tests with testcontainers             |
| ~~Critical~~ | ~~Auto-ACK default is a footgun~~            | ✅ `ack=manual` is now the default                      |
| ~~Critical~~ | ~~No idempotency on send~~                   | ✅ `Idempotency-Key` + atomic SET NX reservation        |
| ~~High~~     | ~~Redis persistence undefined~~              | ✅ `docker-compose.prod.yml` with AOF, auth, noeviction |
| ~~High~~     | ~~Webhook queue is in-memory~~               | ✅ Durable Redis Streams queue + exponential backoff    |
| ~~High~~     | ~~No per-tenant quotas/backpressure~~        | ✅ MAX_RECIPIENT_DEPTH quota (default 10000)            |
| **High**     | Room fan-out is write-amplified              | N members = N queue writes per message                  |
| **Medium**   | No migration/rebuild story for Redis         | Key schema changes are operationally risky              |
| ~~Medium~~   | ~~Webhook signing too weak~~                 | ✅ Timestamped HMAC + per-webhook secrets               |
| ~~Medium~~   | ~~SSRF TOCTOU at webhook delivery~~          | ✅ Re-validate DNS at delivery time                     |
| **Medium**   | No delivery/audit ledger                     | "What happened to message X?" has no answer             |
| ~~Medium~~   | ~~MCP swallows ACK failures~~                | ✅ Warning shown when ACK fails                         |
| **Medium**   | Auth is name-oriented, not account-based     | No immutable IDs, no revocation                         |
| **Low**      | Operational metrics thin                     | Need stream depth, pending age, redelivery counts       |

### Next Priorities

1. Decide data authority: Redis durable vs Postgres source of truth
2. Room fan-out optimization (Redis pub/sub or shared streams)
3. Account-based auth with immutable IDs
4. Delivery/audit ledger for message tracing

---

## In Progress

(none currently)

---

## Recent Changes

| Date       | Commit  | What                                                          |
| ---------- | ------- | ------------------------------------------------------------- |
| 2026-04-12 | —       | Summary Cascade v1: murmur context + murmur decision commands |
| 2026-04-12 | 354d9c8 | Auto-inject messages via murmur inbox + hook commands         |
| 2026-04-12 | 493d445 | MCP server instructions for agent guidance                    |
| 2026-04-12 | c86bb05 | SSRF TOCTOU fix — re-validate URLs at webhook delivery time   |
| 2026-04-12 | ed95df5 | Per-webhook secrets in routes + strip secrets from list API   |
| 2026-04-12 | 839f83c | Webhook retry with exponential backoff                        |
| 2026-04-12 | 6deb7a2 | Atomic idempotency with SET NX prevents race conditions       |
| 2026-04-12 | dccb872 | Room fan-out respects per-recipient queue depth quota         |
| 2026-04-12 | merge   | dev → main: Primitives A/B/C, SSE-only push, usage, 700 tests |
| 2026-04-12 | f338b56 | Durable webhook queue with Redis Streams                      |
| 2026-04-12 | de39ef1 | Rename mcp.py to mcp_server.py (avoid shadowing)              |
| 2026-04-12 | 400fd7c | Production Redis config (AOF, auth, noeviction)               |
| 2026-04-12 | 4ff1b43 | Surface ACK failures in MCP check_messages                    |

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
- SSE-only delivery — no polling of any kind

---

## Contributors

- **Arav** (aravkek) — co-founder, parent Claude directing agents
- **Aarya** (Aarya2004) — co-founder
