# Redis Cost Tightening — Design

**Date:** 2026-04-16
**Status:** Proposed (awaiting plan)
**Owner:** Aarya
**Trigger:** Upstash free tier exhausted in 3 days (516K / 500K monthly cap) with zero real users. Live Fly relay's Redis-backed paths are partially bricked until May 1 monthly reset.

---

## Problem

The Quorus relay burns ~170K Redis commands/day at idle. Three hours of Upstash telemetry on an empty deployment shows:

| Command       | 3h count | Source                                                        |
| ------------- | -------- | ------------------------------------------------------------- |
| `EVAL`        | 45K      | `_WEBHOOK_PROMOTE_LUA` in `RedisWebhookQueueBackend.fetch()`  |
| `XAUTOCLAIM`  | 45K      | Stale-claim reclaim in same `fetch()`                         |
| `XREADGROUP`  | 45K      | New-job read in same `fetch()`                                |
| `ZRANGEBYSCORE` | 45K    | Inside the EVAL Lua (counted separately by Upstash)           |
| `HGETALL`     | 15K      | `RedisPresenceBackend.list_all()` called from `/health`       |
| `EVALSHA`/`ZREMRANGEBYSCORE`/`ZCARD` | 4K each | Sliding-window rate-limit middleware on every HTTP request |
| `EXPIRE`     | 1.3K     | Same rate-limit Lua                                            |

Sustained throughput: ~4 cmds/sec, 24/7, regardless of traffic.

### Root causes

1. **Idle-loop amplification.** `WebhookService._worker_loop_durable()` (`quorus/services/webhook_svc.py:405`) calls `queue_backend.fetch(count=10)` then sleeps 1.0s if empty. Each `fetch()` issues `EVAL promote_delayed` + `XAUTOCLAIM` + `XREADGROUP` = 3 commands (4 with the inner `ZRANGEBYSCORE`). **~250K commands/month per worker, idle.**
2. **Presence list_all fan-out.** `RedisPresenceBackend.list_all()` (`quorus/backends/redis_backends.py:764-794`) issues one `ZRANGE` + N pipelined `HGETALL`s per call (one per agent ever seen in the tenant). It's called from many endpoints: `/agents`, `/presence`, `/usage`, `/rooms/{id}/state`, `/health/detailed`, `/messages` participants. The TUI hub and dashboard poll these endpoints every few seconds. With ~12 agents in the demo tenant and one open TUI ticking every 5s, that's ~17K HGETALLs/day. The `/health` endpoint itself is already minimal — only `/health/detailed` walks presence, so the spec's "split health" change (#2) is a no-op; the real fix is caching `list_all` results.
3. **`NotFoundRateLimitMiddleware` runs on every request.** `quorus/relay.py:581` — even though it only counts true 404s, it calls `rate_limit_svc.is_rate_limited()` (read) on every inbound request to decide whether to short-circuit. Each call = 1-2 Redis ops (`EVALSHA` for sliding-window read + occasional `ZREMRANGEBYSCORE` cleanup). On Fly health probes that's free traffic. **~30K/month from probes + miscellaneous public-internet 404 scanners.**

### Impact today

- Free tier monthly counter resets May 1. Until then, Redis writes return errors, propagated as `RedisOperationTimeout` after 10s wait. Any code path that hits Redis is degraded:
  - Rate limiting fails open (silent — could let abuse through)
  - Lock claim/release errors out
  - SSE message delivery via streams stalls (in-memory SSE still works for connected clients)
  - Webhook delivery halts
- Postgres remains source of truth for messages — no permanent data loss risk.

---

## Goals

1. Cut idle Redis throughput by ≥95% — target <25K commands/month at zero traffic.
2. Make per-request Redis cost proportional to actual work, not poll cadence.
3. Restore the live relay before May 1 by recreating the Upstash database (operational, not in-code).
4. No behavior change visible to users: messages, locks, presence, rate limiting all still work.

### Non-goals

- Removing Redis entirely (out of scope; Postgres-as-queue is a future option).
- Touching the SQLite/in-memory backends (only the Redis path matters for cost).
- Multi-region Redis or replicas.

---

## Approach

Three changes, ordered by risk:

### 1. Webhook worker: blocking reads + adaptive idle backoff

**Today:** poll `fetch()` every 1s.

**New:** When the durable Redis backend is in use, the worker uses native blocking `XREADGROUP ... BLOCK 30000`. One Redis command per actual job (or one per 30s when idle), instead of 3-4/sec.

**Mechanics:**
- Add a new method `RedisWebhookQueueBackend.fetch_blocking(count, block_ms)` that issues a single `XREADGROUP` with `BLOCK block_ms`. Returns immediately on new jobs, or after `block_ms` with empty list.
- Reclaim of stale entries (`XAUTOCLAIM`) and promotion of delayed entries (`EVAL promote_delayed`) move out of the per-fetch hot path. They run on a separate slow timer — every 30s of wall-clock — guarded by a "last ran" timestamp on the queue backend instance.
- Promote-delayed runs only when there's something in the delay set. Cache `ZCARD webhook:delay` for 10s; skip the `EVAL` if the cached value is 0.
- For the in-memory queue path (`_worker_loop_memory`), no change — that path doesn't touch Redis.

**Idle math:** 1 `XREADGROUP` per 30s + 1 `XAUTOCLAIM` per 30s + 1 `ZCARD` per 10s (only when delay set seen non-empty) = ~12K commands/month idle, down from ~250K.

**Why not fully event-driven:** Reclaim of stale entries from crashed workers can't be event-driven — it has to be timer-based by definition. 30s reclaim resolution is fine; webhook delivery SLAs are seconds-to-minutes, not sub-second.

### 2. Cache `presence.list_all()` per tenant

**Today:** `RedisPresenceBackend.list_all(tenant_id, timeout)` issues `ZRANGE index_key + WITHSCORES` followed by a pipelined `HGETALL` per agent. Each TUI/dashboard tick costs (1 + N) Redis commands per tenant.

**New:** Add a small TTL cache inside `RedisPresenceBackend`:
- Key: `(tenant_id, timeout_seconds)`. Value: `(result_list, fetched_at)`.
- TTL: 5 seconds (tunable via `QUORUS_PRESENCE_CACHE_TTL` env var, default 5).
- The cache is process-local — when running multiple Fly instances each holds its own; that's fine because presence is best-effort and per-instance staleness is bounded.
- Heartbeat writes (`heartbeat()`) invalidate the cache for that tenant so the next read sees fresh data within ms (caller's own write is visible).
- Already-shipped `list_all_pipeline` PR (commit `52042be`) reduced this to 2 round-trips. The cache cuts to ~1 fetch per 5s per tenant per process.

**Idle math:** With one TUI open polling 1× per 5s, before: ~17K HGETALLs/day (12 agents). After: ~12 HGETALLs / 5s window = same cost per fetch but only **0.2 fetches/sec → ~17K/day → ~17K/month** (cached lookups don't hit Redis at all). With cache: ~17K/day × 1/5s amortization ≈ ~3K/day.

**Migration:** None — cache is transparent; semantics preserved within 5s window.

### 3. `NotFoundRateLimitMiddleware` skips the read on cached-good paths

**Today:** `NotFoundRateLimitMiddleware.dispatch()` (`quorus/relay.py:581`) calls `rate_limit_svc.is_rate_limited()` on **every** request to decide whether to short-circuit — even though the limit only matters when the response would actually be a 404. The result: 1 `EVALSHA` per request, including dashboard pollers and TUI auth refreshes.

**Today is already partially correct:** `_NOT_FOUND_EXEMPT_PATHS` (line 578) exempts `/health`, `/health/detailed`, `/metrics`. Those don't burn the limiter.

**New:** Maintain a process-local LRU of `(client_ip, path)` tuples that have been seen returning <404 within the last 60s. On request entry, if the tuple is in the LRU, skip the Redis read entirely. On non-404 response, insert the tuple into the LRU. Cache size 4096 entries; eviction is by access time. This makes repeated requests from the same client to known-good paths free.

Optional secondary: defer the `is_rate_limited` read until **after** `call_next` and only do it when the response is a 404. That's better but riskier — it means a flood of 404s from one IP could each issue a Redis call before they get blocked. Keep the check up-front but cache its skip decision.

**Idle math:** Cuts ~30K/month. Realistic floor: any new IP with no LRU entry still pays one read.

### Combined target

Idle: **~20K commands/month** (down from ~5M projected at current rate). Comfortably under the 500K Upstash free tier cap, with headroom for actual usage.

---

## Components changed

| File                                       | Change                                                                                          |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `quorus/backends/redis_backends.py`        | Add `RedisWebhookQueueBackend.fetch_blocking()`; gate `promote_delayed`/`XAUTOCLAIM` with timer + cache. |
| `quorus/services/webhook_svc.py`           | `_worker_loop_durable` switches to blocking fetch; remove unconditional 1.0s sleep.            |
| `quorus/backends/redis_backends.py` (`RedisPresenceBackend.list_all`, ~line 764) | Wrap with TTL cache; invalidate on heartbeat write. |
| `quorus/relay.py` (`NotFoundRateLimitMiddleware`, line 581)        | Process-local LRU of `(ip, path)` non-404 hits to skip the Redis read. |
| `tests/test_webhook_durable.py` (new or extend) | Cover blocking fetch returning early on enqueue, returning empty on timeout, reclaim timer firing on schedule. |
| `tests/test_presence_cache.py` (new)       | Assert `list_all` hits Redis once per cache window; heartbeat invalidates. |
| `tests/test_not_found_lru.py` (new) | Assert second request to a known-good path skips `is_rate_limited`. |

No interface changes to `WebhookQueueBackend` protocol — `fetch_blocking` is additive, in-memory backend can implement it as a wait-on-asyncio.Queue.

---

## Risks & mitigations

| Risk                                                                                  | Mitigation                                                                                                  |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Blocking `XREADGROUP` holds a Redis connection per worker.                            | One worker per relay process; Upstash free tier allows 30 connections. Set explicit `BLOCK 30000` so the connection isn't pinned forever — a 30s timeout still allows pool rebalancing. |
| Reclaim of stale entries delayed up to 30s.                                           | Webhook delivery is not real-time. SLA is seconds-to-minutes. 30s reclaim is well within tolerance.        |
| `/health` change might break monitoring dashboards that scrape the rich JSON.         | Keep `/health/detailed` returning the exact same shape. Search the codebase + website repo for `/health` consumers and migrate them. |
| Rate-limit allowlist could be exploited by spoofing `X-Forwarded-For` to look like Fly. | The allowlist is path-based, not IP-based. Health endpoints simply have no rate limit at all — they have no side effects worth limiting. |
| Recreating Upstash db loses in-flight Redis state (unacked stream entries, locks, rate-limit windows). | Acceptable. Postgres outbox + history is source of truth. At-least-once redelivery resumes on next worker tick. Rate-limit windows reset (worst case: brief over-budget window for one user). |

---

## Operational follow-ups (out of band, not in spec)

1. **Recreate Upstash db** to reset the monthly counter. Steps:
   - Provision a fresh free-tier Upstash Redis db in `ca-central-1`.
   - Update Fly secret: `fly secrets set REDIS_URL=<new-url> -a quorus-relay`.
   - Fly auto-redeploys; verify `/health` returns `redis: connected`.
   - Old db can be deleted after 24h (in case of rollback).
2. Consider provisioning a second free-tier db as a hot standby for next time.
3. Document the new monthly burn budget in CONTEXT.md after deploy.

---

## Testing

- Unit: blocking fetch returns early on enqueue, returns empty list on block timeout, doesn't fire `promote_delayed` when delay set is empty.
- Unit: `/health` does not call any presence methods (mock + assert).
- Unit: rate-limit middleware skips counted increment on `/health`.
- Integration: spin a real Redis (testcontainer or `fakeredis`), enqueue a webhook job, assert worker delivers within 100ms (proves blocking read wakes immediately).
- Burn-rate sanity: log Redis command count for 60s with no traffic; assert ≤ 5 commands.

---

## Acceptance criteria

- `pytest -v` passes including new tests.
- `ruff check .` passes.
- A 60s idle window on the relay produces ≤ 5 outbound Redis commands (verified by enabling `redis.client` debug logging or by counting ops on the Upstash dashboard after deploy).
- `/health` response time < 50ms p99 (vs. current ~200ms when presence is large).
- Live relay's Upstash `Daily Commands` chart drops by ≥ 95% within 24 hours of deploy.
