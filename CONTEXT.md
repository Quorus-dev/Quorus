# Murmur — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-04-13 05:15 UTC

---

## Current State

Murmur (package: murmur-ai) is the universal communication substrate for AI agent swarms. "VS Code Live Share for AI Agents" — any model, any machine, any platform coordinates in real-time.

**Branch:** `main` (869 tests passing) — dev merged to main on 2026-04-12.

**Package:** `pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"`

**Setup (3 commands):**

```bash
pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"
murmur init <your-name> --relay-url <url> --secret <secret>
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
| murmur/tui_hub.py           | ~730   | Full-screen TUI hub: `murmur begin` opens interactive Rich terminal UI                   |
| murmur/backends/            | ~900   | In-memory + Redis backends for all state (incl. RoomStateBackend)                        |
| tests/                      | ~8700  | 871 tests: relay, mcp, config, CLI, usage, agents, room_state, sdk, tui_hub, integration |

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
- JWT auth + API keys, rate limiting on ALL write + history endpoints (60/min msgs, 20/min history, 10/min room create, 30/min join/DM)
- Docker + Railway/Render deploy configs
- Reply threading (reply_to field + Room.reply() SDK)
- **SDK Primitive A/B**: Room.lock(), Room.unlock(), Room.state() — full client-side mutex + state surface with JWT refresh-on-401
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
- **Summary Cascade v2**: `murmur context --summarize [--model M]` — LLM-powered 2-3 paragraph brief from raw context data, actionable for agent injection
- **CRA (murmur resolve)**: `murmur resolve [--room R] [--model M]` — AI-powered git merge conflict resolution using room history for agent intent
- **Decision recording**: `murmur decision <room> "<text>"` — writes to room state decisions via POST /rooms/{room}/state/decisions; surfaced in `murmur context`
- **Hook auto-injection**: `murmur hook enable` now runs `murmur inbox --quiet && murmur context --quiet` on every UserPromptSubmit
- **Portable join tokens**: `murmur share <room>` generates portable token; `murmur quickjoin <token> --name <name>` joins with zero config
- **TUI Hub**: `murmur begin` opens full-screen interactive terminal UI with rooms, agents, live chat; warm first-run wizard with auto-detect relay
- **Doctor diagnostics**: `murmur doctor` runs 13 checks incl. MCP server registration, relay version, hook status; shows web console link
- **Web Console**: Browser-based dashboard at murmur-ai.dev/console for monitoring swarms without CLI

**Tests:** 869 passing + 23 Redis integration tests (require Docker). 272 security tests. Stress tested: 281 msg/s, p50=3.6ms.

**Public relay:** Active via localhost.run tunnel (URL shared privately)

---

## Production Readiness

**Current rating:**

| Stage                   | Score  | Notes                                         |
| ----------------------- | ------ | --------------------------------------------- |
| Trusted private demo    | 9.1/10 | Solid for internal/team use                   |
| Controlled public alpha | 7.8/10 | Ready with published limitations              |
| Paid early access       | 7.0/10 | Needs clear limits, no hard SLA               |
| Serious production SaaS | 6.3/10 | Blockers below must be resolved               |
| High-scale (millions)   | 4.3/10 | Needs load tests, sharding, chaos engineering |

### Delivery Guarantees (Honest Assessment)

| Path                     | Guarantee                   | Caveats                                                                    |
| ------------------------ | --------------------------- | -------------------------------------------------------------------------- |
| HTTP client + manual ACK | At-least-once to caller     | Only for DM inbox path; caller must call `result.ack()`                    |
| Room messages            | Best-effort fan-out         | Postgres-first, but fan-out can fail after commit (fallback: poll history) |
| MCP tools                | At-least-once to MCP server | ACK before tool result reaches Claude                                      |
| SSE                      | Live notifications only     | **Not durable** — use for UX, not delivery guarantees                      |
| Webhooks                 | At-least-once (queue)       | Redis Streams + DLQ, but app-level SSRF checks only                        |

**Key limitation:** No transactional outbox. Room send can commit to history but fail fan-out. This is documented eventual consistency, not a delivery guarantee.

### Remaining Blockers for Production SaaS

| Priority   | Issue                          | Impact                                          | Path to Fix                            |
| ---------- | ------------------------------ | ----------------------------------------------- | -------------------------------------- |
| **High**   | Console accepts shared secrets | Not SaaS-ready auth model                       | First-party auth or key-only           |
| **High**   | Webhook SSRF app-level only    | httpx re-resolves DNS; no egress enforcement    | Egress proxy or network policy         |
| **High**   | No load/chaos testing          | Unproven concurrency envelope                   | k6/locust tests, failure injection     |
| **Medium** | Signup abuse controls          | 5/hr IP limit only; no email verify/CAPTCHA     | Add verification, tenant quotas        |
| **Medium** | Thin operational metrics       | No fan-out failures, DLQ age, ACK lag metrics   | Domain-specific Prometheus counters    |
| **Medium** | No runbooks/alerts             | Ops flying blind                                | Document failure modes, set thresholds |
| **Medium** | No admin tools                 | No DLQ replay, user suspension, stuck queue fix | Admin CLI/API                          |
| **Low**    | Simple RBAC                    | No org roles, key scopes, service accounts      | Expand role model                      |

### Resolved Issues (for reference)

- ✅ Redis + Postgres integration tests in CI
- ✅ `ack=manual` default (no silent message loss)
- ✅ `Idempotency-Key` + body binding + 409 on mismatch
- ✅ Room membership checks on all state/lock endpoints
- ✅ Redis Lua scripts for distributed locks
- ✅ Web console proxy removed (Vite static site)
- ✅ Redis AOF persistence configured
- ✅ Durable webhook queue (Redis Streams + DLQ)
- ✅ Per-tenant backpressure (XLEN check before XADD)
- ✅ Timestamped HMAC webhook signing
- ✅ API key in memory only, never sessionStorage
- ✅ **Transactional outbox** — atomic Postgres writes + background worker fan-out (USE_OUTBOX=true)
- ✅ **Audit ledger** — message lifecycle events (MESSAGE*CREATED → FANOUT*_ → DELIVERED); API at /v1/audit/_
- ✅ **Account-based identity** — participant_id in JWT claims + migration 008 adds FK columns to tables

### What This Means

**Ship as:** Controlled early-access beta with published limitations, no hard delivery SLA, webhook caveats, and monitored tenant limits.

**Do not market as:** Fully production-ready infrastructure with contractual delivery guarantees.

### Next Priorities

1. **Outbox pattern** — transactional room sends with guaranteed fan-out
2. **Audit ledger** — event log for debugging, abuse, incidents
3. **Account-based identity** — immutable IDs, proper revocation
4. **Load testing** — prove the concurrency envelope
5. **Egress controls** — network-level webhook SSRF protection

---

## In Progress

(none currently)

---

## Recent Changes

| Date       | Commit    | What                                                                     |
| ---------- | --------- | ------------------------------------------------------------------------ |
| 2026-04-13 | cd67ddc   | feat: real CLI demos in AgentShowcase — official screenshots/GIFs        |
| 2026-04-13 | f3881b2   | feat: use real brand logos instead of fake SVGs                          |
| 2026-04-13 | 674c314   | fix: remove fake stats, fix page jumping, correct integrations           |
| 2026-04-13 | d8a96c4   | feat: update TUI accent to teal (#14b8a6)                                |
| 2026-04-12 | 5436cfd   | fix: murmur join preserves config when no flags provided                 |
| 2026-04-12 | 5d74dbc   | feat: account-based identity — participant_id in JWTs, migration 008     |
| 2026-04-12 | (pending) | feat: audit ledger — message lifecycle events with API at /v1/audit/\*   |
| 2026-04-12 | (pending) | feat: transactional outbox — atomic Postgres + background worker fan-out |
| 2026-04-12 | fd741e5   | refactor: migrate website from Next.js to Vite — removes proxy SSRF risk |
| 2026-04-12 | 5250d7b   | fix: DNS rebinding protection in relay proxy (resolve before fetch)      |
| 2026-04-12 | 2f2abfb   | ci: add Postgres integration tests and CI job (migrations + CRUD)        |
| 2026-04-12 | 00eebad   | ci: add website lint and build job                                       |
| 2026-04-12 | 679fa13   | fix: add .nvmrc and .node-version for Node 20 enforcement                |
| 2026-04-12 | 9f7ee4f   | fix: fail closed if RELAY_ALLOWLIST unset in production                  |
| 2026-04-12 | e294225   | fix: make room fan-out failures non-fatal after history commit           |
| 2026-04-12 | f2983ee   | fix: console credential handling — no sessionStorage for keys, warning   |

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
