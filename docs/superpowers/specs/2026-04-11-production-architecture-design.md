# Production Architecture Design — Murmur

**Date:** 2026-04-11
**Branch:** productionize
**Status:** Approved

## Overview

Port v1's production infrastructure (Postgres, per-agent API keys, JWT auth, Alembic migrations) into the productionize branch's feature-rich relay (rooms, SSE, webhooks, presence, web dashboard, CLI). Drop the tunnel abstraction from v1 — rooms replace it. Postgres required, Redis optional (future multi-replica).

## Decisions

- **Deployment model:** Self-hosted now, designed for multi-tenant managed service later
- **Infrastructure:** Postgres required, Redis optional (for multi-replica SSE/rate-limits)
- **Auth:** API keys (C) — per-agent API keys for normal operations, scoped tokens for SSE/invites
- **Schema:** Keep tenants, drop tunnels, rooms replace them (B)
- **Migration:** Clean break — no JSON migration path, no one has deployed yet
- **Package:** Keep `murmur/` package name, port v1 code into `murmur/storage/`, `murmur/auth/`, `murmur/admin/`
- **API key storage:** No encryption in config file — file permissions (0600), keys are revocable, same threat model as SSH keys

## 1. Postgres Schema

### Ported from v1 (tunnel table dropped)

**tenants**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID (String 36) | PK, auto-generated |
| slug | Text | Unique, validated |
| display_name | Text | |
| created_at | DateTime(tz) | |

**participants**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| tenant_id | UUID | FK -> tenants.id, CASCADE |
| name | Text | Unique per tenant |
| role | Text | "admin" or "user" |
| created_at | DateTime(tz) | |

**api_keys**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| participant_id | UUID | FK -> participants.id, CASCADE |
| label | Text | Nullable, human-readable |
| key_prefix | String(16) | Unique, for fast lookup |
| key_hash | Text | bcrypt(sha256(raw_key)) |
| revoked_at | DateTime(tz) | Nullable |
| created_at | DateTime(tz) | |

### New tables for productionize features

**rooms**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| tenant_id | UUID | FK -> tenants.id, CASCADE |
| name | Text | Unique per tenant, 1-64 chars |
| created_by | Text | Agent name who created |
| created_at | DateTime(tz) | |

**room_members**
| Column | Type | Notes |
|--------|------|-------|
| room_id | UUID | FK -> rooms.id, CASCADE |
| participant_name | Text | Agent name |
| joined_at | DateTime(tz) | |
| | | PK on (room_id, participant_name) |

**messages**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| tenant_id | UUID | FK -> tenants.id, indexed |
| from_name | Text | Sender agent name |
| to_name | Text | Nullable — set for DMs |
| room_id | UUID | Nullable FK -> rooms.id — set for room messages |
| content | Text | Message body |
| message_type | Text | "chat", "direct", "broadcast", "claim", "status", "request", "alert", "sync" |
| timestamp | DateTime(tz) | |
| chunk_group | UUID | Nullable — for chunked messages |
| chunk_index | Integer | Nullable |
| chunk_total | Integer | Nullable |
| delivered_at | DateTime(tz) | Nullable — NULL = undelivered DM |

Indexes: (tenant_id, to_name, delivered_at) for DM fetch, (tenant_id, room_id, timestamp) for room history.

**Delivery model:** DMs use `to_name` + `delivered_at` (NULL = undelivered, set on read). Room messages use `room_id` — stored once, all members read via room history query (no per-member delivery tracking). This means room messages don't appear in `GET /messages/{recipient}` — they're accessed via `GET /rooms/{id}/history`.

**webhooks**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| tenant_id | UUID | FK -> tenants.id |
| participant_name | Text | Nullable — per-agent webhook |
| room_id | UUID | Nullable FK -> rooms.id — per-room webhook |
| url | Text | Callback URL |
| created_at | DateTime(tz) | |

**presence**
| Column | Type | Notes |
|--------|------|-------|
| tenant_id | UUID | PK part 1 |
| participant_name | Text | PK part 2 |
| last_heartbeat | DateTime(tz) | |
| status | Text | "active", "idle", "busy" |
| room_id | UUID | Nullable — current room |
| uptime_start | DateTime(tz) | |

### Alembic migrations

- `001_initial_admin_schema.py` — tenants, participants, api_keys (port from v1, drop tunnels)
- `002_rooms_and_messages.py` — rooms, room_members, messages, webhooks, presence

## 2. Auth & Identity Model

### Auth flow

1. Admin creates tenant: `POST /v1/tenants` (requires `BOOTSTRAP_SECRET`)
2. Admin creates participants: `POST /v1/tenants/{slug}/participants` (requires admin JWT)
3. Admin issues API keys: `POST /v1/tenants/{slug}/participants/{name}/keys` — returns raw key once (`mct_{lookup}_{secret}`)
4. Agent exchanges API key for JWT: `POST /v1/auth/token` — short-lived JWT with claims: `sub`, `tenant_id`, `tenant_slug`, `role`
5. All relay endpoints require JWT. Middleware enforces identity.

### Identity enforcement

| Endpoint | Enforcement |
|----------|------------|
| POST /messages | from_name must match JWT sub |
| GET /messages/{recipient} | recipient must match JWT sub |
| POST /rooms/{id}/messages | from_name must match JWT sub, must be room member |
| GET /stream/{recipient} | recipient must match JWT sub |
| POST /heartbeat | instance_name must match JWT sub |
| Room create/join/leave | agent identity from JWT |
| Room kick/destroy/rename | must be room creator or admin role |

### Legacy fallback

`RELAY_SECRET` still works as Bearer token. When used, `auth.sub = None`, identity enforcement skipped (admin-level access). Allows existing MCP clients to work until updated.

### Scoped tokens (kept as-is)

- SSE tokens: short-lived, recipient-scoped
- Invite tokens: HMAC-signed, room-scoped, time-limited

### Token modules (ported from v1)

- `murmur/auth/tokens.py` — JWT create/decode, API key generate/hash/verify
- `murmur/auth/middleware.py` — JWT-first verification with legacy fallback, revocation cache
- `murmur/auth/routes.py` — API key -> JWT exchange endpoint

## 3. Web Dashboard & Key Management UI

### Dashboard auth

- GET / shows login form on first visit
- Login: paste API key (exchanged for JWT, stored in localStorage) or use RELAY_SECRET for admin
- JWT sent on all dashboard API calls, handle expiry with re-prompt

### Key management (admin only)

- View participants: list all agents with status
- Create participant: form for name + role
- Issue key: generate new key, display once with copy button
- Revoke key: button per key with confirmation
- Rotate key: issue new + revoke old (UI convenience, two API calls)

### Invite pages

- GET /invite/{room} stays public
- Joining via invite registers agent as "user" participant if not exists, issues API key displayed on success page

## 4. Storage Backend

### What uses Postgres (via SQLAlchemy)

- Rooms, room_members, webhooks, presence — CRUD on their own tables via `get_db_session()`
- Messages — INSERT for send, SELECT + UPDATE for delivery, query by room_id for history

### What uses QueueBackend protocol

- DM delivery queue semantics: enqueue = INSERT into messages, dequeue_all = SELECT WHERE to_name=? AND delivered_at IS NULL + UPDATE delivered_at
- Backend `scoped()` changes from `(tenant_id, tunnel_id)` to `(tenant_id)`

### What stays in-memory

- SSE queues (`asyncio.Queue` per connection) — connection-scoped, rebuilt on reconnect
- `asyncio.Event` for long-poll wakeup — process-local signaling
- `asyncio.Lock` for concurrency control — process-local
- Rate limiting buckets — sliding window, reset on restart is acceptable
- Analytics counters — exposed via Prometheus, not persisted to Postgres

## 5. Relay Module Split

Current `murmur/relay.py` (~1200 lines) splits into:

| Module | Purpose |
|--------|---------|
| `murmur/relay.py` | FastAPI app setup, lifespan (Postgres init, migrations), middleware, CORS, shared config |
| `murmur/relay_routes.py` | Message, room, SSE, webhook, presence, health endpoints |
| `murmur/storage/__init__.py` | Package init |
| `murmur/storage/backend.py` | QueueBackend protocol (ported from v1, scoped() simplified) |
| `murmur/storage/memory.py` | InMemoryBackend (ported from v1) |
| `murmur/storage/postgres.py` | Async engine, session factory (ported from v1) |
| `murmur/auth/__init__.py` | Package init |
| `murmur/auth/tokens.py` | JWT + API key crypto (ported from v1) |
| `murmur/auth/middleware.py` | Auth verification dependency (ported from v1) |
| `murmur/auth/routes.py` | Token exchange endpoint (ported from v1) |
| `murmur/admin/__init__.py` | Package init |
| `murmur/admin/models.py` | SQLAlchemy ORM models (ported from v1, tunnels dropped, rooms/messages/webhooks/presence added) |
| `murmur/admin/routes.py` | Tenant, participant, key CRUD (ported from v1, tunnel routes dropped) |

## 6. Docker & Deployment

### docker-compose.yml

```yaml
services:
  relay:
    build: .
    ports:
      - "${PORT:-8080}:8080"
    environment:
      RELAY_SECRET: ${RELAY_SECRET:-}
      DATABASE_URL: ${DATABASE_URL:-postgresql+asyncpg://murmur:murmur@postgres:5432/murmur}
      JWT_SECRET: ${JWT_SECRET:-}
      JWT_ALGORITHM: ${JWT_ALGORITHM:-HS256}
      JWT_TTL_SECONDS: ${JWT_TTL_SECONDS:-86400}
      BOOTSTRAP_SECRET: ${BOOTSTRAP_SECRET:-}
      PORT: ${PORT:-8080}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      MAX_MESSAGES: ${MAX_MESSAGES:-1000}
      MESSAGE_TTL_SECONDS: ${MESSAGE_TTL_SECONDS:-86400}
      RATE_LIMIT_WINDOW: ${RATE_LIMIT_WINDOW:-60}
      RATE_LIMIT_MAX: ${RATE_LIMIT_MAX:-60}
    depends_on:
      postgres:
        condition: service_healthy

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: murmur
      POSTGRES_USER: murmur
      POSTGRES_PASSWORD: murmur
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U murmur"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pgdata:
```

### Startup sequence

1. Connect to Postgres (fail fast if unavailable)
2. Run Alembic migrations (auto-upgrade to head)
3. Initialize storage backend
4. Start FastAPI app

### Health endpoint

Updated: `GET /health` checks Postgres connectivity, returns `{"status": "ok", "postgres": "connected"}` or 503.

### Environment variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| DATABASE_URL | Yes | - | Postgres connection string (asyncpg) |
| JWT_SECRET | Yes (prod) | - | HMAC key for JWT signing, min 32 bytes |
| BOOTSTRAP_SECRET | Yes (first setup) | - | For creating first tenant |
| RELAY_SECRET | No | - | Legacy auth fallback |
| JWT_ALGORITHM | No | HS256 | HS256/HS384/HS512 |
| JWT_TTL_SECONDS | No | 86400 | JWT lifetime |
| PORT | No | 8080 | |
| LOG_LEVEL | No | INFO | |
| MAX_MESSAGES | No | 1000 | Per-recipient DM queue limit |
| MESSAGE_TTL_SECONDS | No | 86400 | Message expiry |
| RATE_LIMIT_WINDOW | No | 60 | Seconds |
| RATE_LIMIT_MAX | No | 60 | Requests per window |

## 7. MCP Client Updates

### Config changes

`~/.mcp-tunnel/config.json` gains `api_key` field:
```json
{
  "relay_url": "https://relay.example.com",
  "api_key": "mct_abc123_def456...",
  "instance_name": "my-agent"
}
```

### Client auth flow

1. If `api_key` set: exchange for JWT via `POST /v1/auth/token`, cache JWT
2. Use JWT as Bearer token for all requests
3. On 401: re-exchange API key for new JWT, retry once
4. If only `relay_secret` set: use legacy Bearer auth

### CLI changes

- `murmur init <name> --relay <url> --api-key <key>` (new)
- `murmur init <name> --relay <url> --secret <secret>` (legacy, still works)
- Config file set to 0600 permissions

## 8. Testing Strategy

### Test categories

| Category | What | Source |
|----------|------|--------|
| Auth unit tests | JWT create/decode, API key generate/hash/verify | Port from v1 |
| Auth middleware tests | JWT verification, legacy fallback, revocation | Port from v1 |
| Auth route tests | API key -> JWT exchange | Port from v1 |
| Admin route tests | Tenant/participant/key CRUD | Port from v1, drop tunnel tests |
| Storage protocol tests | QueueBackend compliance against InMemoryBackend | Port from v1 |
| Identity enforcement tests | Agent A can't read B's inbox, can't send as B | New |
| Room auth tests | Membership required, admin ops require creator/admin | New |
| Integration tests | Full flow: create tenant -> issue key -> exchange JWT -> send -> receive | New |
| Existing relay tests | Update 503 tests to use JWT fixtures instead of shared secret | Update |

### Test infrastructure

- Real Postgres for tests (not SQLite) — pytest fixture creates test DB, runs migrations, tears down
- Auth fixtures: helper to create tenant + participant + API key + JWT for test setup
- Target: all existing functionality works with new auth layer on top

## 9. Files to Create/Modify

### New files
- `murmur/storage/__init__.py`
- `murmur/storage/backend.py`
- `murmur/storage/memory.py`
- `murmur/storage/postgres.py`
- `murmur/auth/__init__.py`
- `murmur/auth/tokens.py`
- `murmur/auth/middleware.py`
- `murmur/auth/routes.py`
- `murmur/admin/__init__.py`
- `murmur/admin/models.py`
- `murmur/admin/routes.py`
- `murmur/relay_routes.py`
- `murmur/migrations/` (alembic setup + version files)
- `alembic.ini`
- `tests/test_auth.py`
- `tests/test_admin.py`
- `tests/test_storage.py`
- `tests/test_identity.py`

### Modified files
- `murmur/relay.py` — slim down to app setup + lifespan, delegate routes
- `murmur/mcp_server.py` — API key exchange, JWT caching, refresh
- `murmur/cli.py` — `murmur init` gains `--api-key` flag
- `murmur/config.py` — `api_key` field support
- `docker-compose.yml` — add Postgres, remove MESSAGES_FILE
- `Dockerfile` — add asyncpg/alembic deps
- `pyproject.toml` — add sqlalchemy, asyncpg, alembic, pyjwt, bcrypt
- `tests/test_relay.py` — update auth fixtures
- `tests/test_cli.py` — update for new init flow
- Dashboard HTML in relay.py — add login, key management UI
