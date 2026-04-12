# Murmur — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-04-12 02:15 EDT

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
| murmur/mcp_server.py | ~650  | MCP server: 10+ tools, SSE listener, auto-poll, heartbeat, lazy poll mode                                                                         |
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

**Tests:** 611 passing, all green + 9 Redis integration tests. 272 security tests. Stress tested: 281 msg/s, p50=3.6ms.

**Public relay:** Active via localhost.run tunnel (URL shared privately)

---

## Production Readiness

**Current rating:** Private demo 8.8/10 | Public alpha 7.2/10 | Production SaaS 5.2/10

### Delivery Guarantees

Manual ACK can provide client-confirmed delivery for callers that explicitly ACK only after successful processing. The API still permits unsafe auto-ACK and some client surfaces make correct usage easier than others.

| Path | Guarantee | Notes |
|------|-----------|-------|
| HTTP client + manual ACK | At-least-once to caller | Caller must call `result.ack()` after processing |
| HTTP client + auto ACK | At-most-once | Messages deleted before caller processes — **footgun** |
| MCP tools | At-least-once to MCP server | ACK happens after formatting, before tool result reaches Claude |
| SSE | Live notifications only | Not durable — use for real-time UX, not delivery |
| Webhooks | Best-effort | In-memory queue, no DLQ persistence |

### Blockers for Production SaaS

| Priority | Issue | Status |
|----------|-------|--------|
| ~~Critical~~ | ~~No real Redis/Postgres integration tests~~ | ✅ 9 integration tests with testcontainers |
| ~~Critical~~ | ~~Auto-ACK default is a footgun~~ | ✅ `ack=manual` is now the default |
| ~~Critical~~ | ~~No idempotency on send~~ | ✅ `Idempotency-Key` header support |
| ~~High~~ | ~~Redis persistence undefined~~ | ✅ `docker-compose.prod.yml` with AOF, auth, noeviction |
| **High** | Webhook queue is in-memory | Crashes lose queued jobs and DLQ |
| ~~High~~ | ~~No per-tenant quotas/backpressure~~ | ✅ MAX_RECIPIENT_DEPTH quota (default 10000) |
| **High** | Room fan-out is write-amplified | N members = N queue writes per message |
| **Medium** | No migration/rebuild story for Redis | Key schema changes are operationally risky |
| ~~Medium~~ | ~~Webhook signing too weak~~ | ✅ Timestamped HMAC + per-webhook secrets |
| **Medium** | No delivery/audit ledger | "What happened to message X?" has no answer |
| ~~Medium~~ | ~~MCP swallows ACK failures~~ | ✅ Warning shown when ACK fails |
| **Medium** | Auth is name-oriented, not account-based | No immutable IDs, no revocation |
| **Low** | Operational metrics thin | Need stream depth, pending age, redelivery counts |

### Next Priorities

1. Durable webhook queue (Redis Streams instead of in-memory)
2. Decide data authority: Redis durable vs Postgres source of truth
3. Room fan-out optimization (Redis pub/sub or shared streams)
4. Account-based auth with immutable IDs

---

## In Progress

(none currently)

---

## Recent Changes

| Date       | Commit  | What                                               |
| ---------- | ------- | -------------------------------------------------- |
| 2026-04-12 | 400fd7c | Production Redis config (AOF, auth, noeviction)    |
| 2026-04-12 | 4ff1b43 | Surface ACK failures in MCP check_messages         |
| 2026-04-12 | 261bc89 | Per-webhook secrets + timestamped HMAC signatures  |
| 2026-04-12 | d6f6004 | Per-recipient queue depth quota (MAX_RECIPIENT_DEPTH) |
| 2026-04-12 | 6cb9200 | Redis integration tests with testcontainers        |
| 2026-04-12 | ce2ab69 | Idempotency-Key header support for sends           |
| 2026-04-12 | 3a61c83 | Make ack=manual the default for message fetches    |
| 2026-04-12 | baca2c1 | ReceiveResult.ack() raises AckError on failure     |
| 2026-04-12 | d3f77a4 | MCP auto-poll defers ACK until consumption         |
| 2026-04-12 | 781e971 | Per-message ACK for chunked messages covers all    |

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
