# Murmur — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-04-11

---

## Current State

Murmur (package: mcp-tunnel) is a relay-based system for inter-agent communication across distributed Claude Code instances. A central HTTP relay stores and forwards messages between named participants.

**What's built (v1 — working):**

- `relay_server.py` (555L) — FastAPI relay with per-recipient queues, file persistence, chunking, analytics, TTL, webhooks, long-polling
- `mcp_server.py` (361L) — Stdio MCP server exposing send_message, check_messages, list_participants
- `tunnel_config.py` (77L) — Config loading (env > file > legacy fallback)
- `analytics.py` (91L) — CLI dashboard with rich tables
- Tests: ~1058 lines across 4 test files, 30+ tests covering auth, concurrency, persistence, chunking, config

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich

**Console entrypoints:** `mcp-tunnel-relay`, `mcp-tunnel-analytics`

---

## In Progress

Nobody is actively coding right now. Planning phase for v1 managed service.

---

## Recent Changes

| Date       | Commit  | What                                                          |
| ---------- | ------- | ------------------------------------------------------------- |
| 2026-04-11 | deccc90 | Fixed TTL-sensitive timestamps in chunk test                  |
| 2026-04-11 | 764cce4 | Validated relay URL with real urllib parsing                  |
| 2026-04-11 | 9742e0a | Enforced message TTL on relay reads (not just sends)          |
| 2026-04-11 | 2d9592a | Wired relay console script to real CLI entrypoint             |
| 2026-04-11 | 51bac9c | Switched to tmp_path fixture for test cleanup                 |
| 2026-04-10 | 122d8a5 | Expanded config tests (legacy fallback, as_bool, edge cases)  |
| 2026-04-10 | 284c6fd | Human-readable uptime in analytics CLI                        |
| 2026-04-10 | 3afd681 | Extracted \_relay_error_message to deduplicate error handling |

---

## Decisions Made

### V1 Managed Service Scope (2026-04-11)

**Ship-blocking (must have):**

1. Redis queue backend with `QueueBackend` protocol interface
2. Postgres for tenants, participants, credentials, tunnels
3. JWT auth with API key credential exchange
4. Tenant isolation (namespace prefix in Redis)
5. Admin REST API (create tenant, manage participants, issue keys)
6. Docker-compose (relay + redis + postgres)
7. Structured logging with tenant context (structlog -> JSON)
8. Prometheus metrics endpoint
9. Health checks (Redis + Postgres connectivity)

**Build next:** TUI management tool, per-tenant rate limiting, OpenTelemetry tracing, audit log in Postgres

**Build later:** RabbitMQ backend, message replay (Kafka), WebUI, exactly-once delivery

### Key Architecture Choices

- **QueueBackend interface now, Redis only.** RabbitMQ deferred — exactly-once is a project in itself. Message replay needs log-based storage (Kafka), not queues.
- **Management UI sequence:** Admin REST API -> TUI (rich/textual) -> WebUI. TUI is v1 interface.
- **Observability:** Prometheus metrics + structlog JSON are ship-blocking. OpenTelemetry tracing is build-next.
- **Current analytics dict gets replaced** by Prometheus counters/histograms.

### Tunnels Schema

```sql
tunnels (
  id uuid primary key,
  tenant_id uuid references tenants,
  name text not null,
  backend text not null default 'redis',
  features jsonb default '{}',
  created_at timestamptz default now()
)
```

---

## Next Up

1. Design `QueueBackend` protocol and `RedisQueueBackend` implementation
2. Postgres schema for tenants, participants, credentials, tunnels
3. JWT auth layer replacing Bearer token
4. Refactor relay_server.py to use QueueBackend instead of in-memory dicts
5. Docker-compose with relay + redis + postgres
6. Structured logging migration (logging.basicConfig -> structlog)
7. Prometheus metrics endpoint

---

## Contributors

- **Arav** (aravkek) — co-founder
- **Aarya** (Aarya2004) — co-founder
