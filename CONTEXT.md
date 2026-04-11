# Murmur — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-04-11

---

## Current State

Murmur (package: mcp-tunnel) is a relay-based system for inter-agent communication across distributed Claude Code instances. A central HTTP relay stores and forwards messages between named participants. **Now supports group chat rooms and real-time SSE push delivery.**

**What's built:**

- `relay_server.py` (~780L) — FastAPI relay with rooms, fan-out messaging, SSE streaming, per-recipient queues, file persistence, chunking, analytics, TTL, webhooks, long-polling
- `mcp_server.py` (~550L) — Stdio MCP server with send_message, check_messages, list_participants, send_room_message, join_room, list_rooms, start_auto_poll, stop_auto_poll + SSE background listener
- `cli.py` (~285L) — Human CLI: `murmur watch/say/dm/rooms/create/invite/members`
- `tunnel_config.py` (77L) — Config loading (env > file > legacy fallback)
- `analytics.py` (91L) — CLI dashboard with rich tables
- Tests: 122 passing across 5 test files (relay, mcp, config, integration, rooms integration)

**Key features added (2026-04-11):**

- Room CRUD (create, list, get, join, leave)
- Room message fan-out (send once, all members receive)
- SSE push delivery (`GET /stream/{recipient}`) — replaces polling
- MCP room tools (send_room_message, join_room, list_rooms)
- SSE background listener in MCP server (replaces polling loop)
- CLI for human participation in rooms
- Security hardening: timing-safe auth, message_type validation, room member cap (50), room message size limits

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich

**Console entrypoints:** `mcp-tunnel-relay`, `mcp-tunnel-analytics`, `murmur`

---

## In Progress

Preparing for YC + OpenAI hackathons on April 16, 2026. Need to deploy relay and do dry run with real agents.

---

## Recent Changes

| Date       | Commit   | What                                             |
| ---------- | -------- | ------------------------------------------------ |
| 2026-04-11 | (wip)    | Auto-poll tools: start_auto_poll, stop_auto_poll |
| 2026-04-11 | 6246aa5  | Merged rooms integration tests                   |
| 2026-04-11 | (merged) | CLI tool with watch/say/dm/rooms/create/invite   |
| 2026-04-11 | (merged) | MCP room tools + SSE push client                 |
| 2026-04-11 | f3698e2  | SSE stream endpoint for real-time push           |
| 2026-04-11 | f070b2f  | Room message fan-out endpoint                    |
| 2026-04-11 | 8bdcd93  | Room state, persistence, CRUD endpoints          |
| 2026-04-11 | b777fd0  | Rooms implementation plan (9 tasks)              |
| 2026-04-11 | 12c6bc4  | Design spec + shared context + CLAUDE.md         |

---

## Decisions Made

### Rooms & Group Chat (2026-04-11)

- **Rooms are broadcast groups** layered on existing per-recipient inbox model
- **Fan-out** copies messages to each member's inbox with `room` field
- **SSE replaces polling** — agents maintain persistent connection, messages arrive instantly
- **Message types** are convention, not enforced (except validation to allowed set): chat, claim, status, request, alert, sync
- **Room member cap:** 50 members per room (configurable via MAX_ROOM_MEMBERS)
- **Agent operating protocol:** claim before build, test after change, git discipline, continuous quality loop — this is a prompt template, not relay enforcement

### V1 Managed Service Scope (2026-04-11)

**Ship-blocking (must have):**

1. Redis queue backend with `QueueBackend` protocol interface
2. Postgres for tenants, participants, credentials, tunnels
3. JWT auth with API key credential exchange
4. Tenant isolation (namespace prefix in Redis)
5. Admin REST API
6. Docker-compose (relay + redis + postgres)
7. Structured logging (structlog -> JSON)
8. Prometheus metrics
9. Health checks

**Build next:** TUI, per-tenant rate limiting, OpenTelemetry, audit log

**Build later:** RabbitMQ, Kafka replay, WebUI, exactly-once delivery

---

## Next Up

1. **Deploy relay for hackathon** — Dockerized, exposed via ngrok, configure 2 rooms (yc-hack, openai-hack)
2. **Dry run** — 2 humans + multiple agents in rooms, end-to-end test
3. **Hackathon agent prompts** — system prompt template for agents joining rooms
4. Post-hackathon: Redis backend, Postgres, JWT auth, managed service

---

## Security Notes (from review 2026-04-11)

**Fixed:** timing-safe auth comparison, message_type validation, room member cap, room message size limit

**Known limitations (acceptable for hackathon):**

- Single shared Bearer token (no per-participant auth)
- SSE token in query param (SSE doesn't support headers)
- No rate limiting (add post-hackathon)
- Messages stored in plaintext JSON on disk

---

## Contributors

- **Arav** (aravkek) — co-founder
- **Aarya** (Aarya2004) — co-founder
