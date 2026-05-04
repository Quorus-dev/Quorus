# Post-Deploy Verification — 5 commands

> **Run these in order, immediately after `flyctl deploy`.**
> Total time: ~2 minutes.
> If ANY command fails, **roll back** with `flyctl releases rollback -a quorus-relay <previous>` before debugging — production traffic is shedding errors while you investigate.

---

**RTO target:** 15 minutes.
**RPO target:** 24 hours (last `pg_dump`).

---

## 1. Relay health (no auth)

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://quorus-relay.fly.dev/health
```

- **Expected:** `200`
- **What it proves:** The Fly machine is up, FastAPI is serving, the `/health` route is wired.
- **Failure modes:**
  - `000` / connection refused → Fly machine never came up. `flyctl logs -a quorus-relay --since 5m` then rollback.
  - `502` / `503` → Fly proxy is up but the app died on boot. Check Sentry + `flyctl logs`. Rollback.
  - `500` → app is up but `/health` is throwing — usually a misconfigured DB URL. Rollback, verify `flyctl secrets list`.

---

## 2. Detailed health (DB + Redis + outbox)

```bash
curl -s https://quorus-relay.fly.dev/health/detailed | jq '{db: .db.ok, redis: .redis.ok, outbox: .outbox.ok, version: .version}'
```

- **Expected:**
  ```json
  { "db": true, "redis": true, "outbox": true, "version": "<deploy-sha>" }
  ```
- **What it proves:** Postgres connection pool is healthy, Redis is reachable, the transactional outbox worker is running, and the version string matches the SHA you just deployed.
- **Failure modes:**
  - `db: false` → Postgres is unreachable. Check `flyctl postgres list` and the secrets in `flyctl secrets list`. **Section 11.2 of `LAUNCH_READINESS.md` runbook applies.**
  - `redis: false` → Upstash quota or auth. **Section 11.1 runbook applies.** Rate limiting will fail open until fixed.
  - `outbox: false` → Background worker died. Restart with `flyctl machine restart -a quorus-relay <id>`.
  - `version` does not match HEAD → release was promoted but cache is stale. Hard-restart: `flyctl restart -a quorus-relay`.

---

## 3. Auth-protected write rejects unauthenticated POST

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://quorus-relay.fly.dev/v1/social/claim \
  -H 'Content-Type: application/json' \
  -d '{"from_name":"smoke","verb":"claim","target":"healthz"}'
```

- **Expected:** `401` (or `403` if a hostile proxy is in front).
- **What it proves:** Auth gate is alive — the social-claim verb requires a valid bearer. If this returns `200`, **legacy admin auth is leaking** and `ALLOW_LEGACY_AUTH` is misconfigured. Roll back immediately.
- **Failure modes:**
  - `200` → **CRITICAL** — auth disabled. Roll back. Verify `fly.toml` has `ALLOW_LEGACY_AUTH = "false"`.
  - `404` → Route was renamed/removed in the deploy and the spec moved. Update this doc.
  - `5xx` → Backend exception in the auth path. Check Sentry. Rollback.

---

## 4. Prometheus /metrics emits counters

```bash
curl -s https://quorus-relay.fly.dev/metrics | grep -cE '^(http_requests_total|quorus_messages_total|quorus_rate_limit_)'
```

- **Expected:** A non-zero integer (typically `>= 3`).
- **What it proves:** The Prometheus instrumentator is registered, the SSE/message counters are emitting, and rate-limit counters are wired. Grafana / Fly metrics will start populating immediately.
- **Failure modes:**
  - `0` → Either `/metrics` is gated (check `include_in_schema=False` is correct) or the Instrumentator never registered. Check `flyctl logs -a quorus-relay | grep -i instrument`.
  - `403` / `401` → Someone added auth in front of `/metrics` — break that. Prometheus scraper has no bearer.
  - `502` → Same as section 1. Rollback.

---

## 5. SSE stream connects within 2s

```bash
timeout 3 curl -sN -H "Authorization: Bearer $RELAY_SECRET" \
  https://quorus-relay.fly.dev/stream/launch-smoke 2>&1 | head -2
```

- **Expected:** First line begins with `event:` or `:` (SSE keep-alive comment) within 2s. Curl exits with code 124 (timeout reached, which is fine — it means the stream stayed open).
- **What it proves:** SSE long-poll path works end-to-end, Fly's proxy is not buffering the response, and the participant resolver accepts the smoke key. This is the single hottest path in production — if SSE is broken, every connected agent stops receiving messages.
- **Failure modes:**
  - Empty output / curl exits non-124 immediately → SSE isn't streaming. Check Fly proxy buffering settings, then `flyctl logs -a quorus-relay | grep -i sse`.
  - `401` body before SSE opens → bearer rejected. Confirm `RELAY_SECRET` env in your shell matches the secret on Fly.
  - `503` → server overloaded, hit `[http_service.concurrency]` cap. Either it's a thundering herd (wait 10s and retry) or autoscaling is misconfigured (`fly.toml` should have `auto_stop_machines = false`).

---

## What "all 5 green" means

If all five commands return their expected output:

- The relay is up, on the right SHA, talking to Postgres + Redis + the outbox worker.
- Auth is enforced.
- Metrics are flowing.
- SSE — the most-trafficked endpoint — is streaming.

You can announce. Tweet thread is in `docs/MARKETING_LAUNCH_TWEET.md`.

If even one is red: roll back first, debug second. Public traffic on a half-broken deploy creates incident reports faster than fixing them.

---

## Rollback (one-liner)

```bash
flyctl releases -a quorus-relay | head -5
flyctl releases rollback -a quorus-relay <previous-version>
# Re-run all 5 commands above against the rolled-back version.
```

The Vercel website does not need rollback alongside the relay — it serves static assets and only fetches read-only `/health/detailed` for the status badge. A relay rollback is invisible to the marketing page.
