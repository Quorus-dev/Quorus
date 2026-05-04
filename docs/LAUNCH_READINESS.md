# Quorus Launch Readiness Checklist

> **Single page. Walk top-to-bottom. Tick every box. <30 minutes.**
> Branch: `feat/may4-sprint`. HEAD reference: `c86b978` (wave-7 7-harness gap closed).
> If any box can't be ticked, **STOP** — fix or rollback before public traffic.

---

## 1. Code green

### 1.1 Pytest — all suites green

- **Check:** Every test passes locally on the deploy commit.
- **Command:**
  ```bash
  cd /Users/aravkekane/Desktop/Quorus && pytest -v --tb=short -x
  ```
- **Pass:** Final line reads `==== 1421 passed in <Ns> ====` (or higher count). Zero failures, zero errors.
- [ ]

### 1.2 Ruff — lint clean

- **Check:** Zero ruff violations.
- **Command:**
  ```bash
  cd /Users/aravkekane/Desktop/Quorus && ruff check .
  ```
- **Pass:** `All checks passed!`
- [ ]

### 1.3 Reflex demo — end-to-end pipeline

- **Check:** Stub-mode demo completes the triage → bid → claim → spawn → reply pipeline.
- **Command:**
  ```bash
  cd /Users/aravkekane/Desktop/Quorus && ./scripts/demo_reflex.sh
  ```
- **Pass:** Exit 0, summary shows `reply received in <2s`, `Total e2e latency: <2s`.
- [ ]

### 1.4 AST sanity — package imports cleanly

- **Check:** No syntax errors in any shipped module.
- **Command:**
  ```bash
  cd /Users/aravkekane/Desktop/Quorus && python -m compileall -q quorus packages
  ```
- **Pass:** Zero output. Exit 0.
- [ ]

### 1.5 Cold-install smoke — pipx-install works

- **Check:** A fresh `pipx install` of HEAD produces a working `quorus` binary.
- **Command:**
  ```bash
  cd /Users/aravkekane/Desktop/Quorus && ./scripts/cold_install_smoke.sh
  ```
- **Pass:** `cold-install smoke OK` printed. Exit 0.
- [ ]

---

## 2. Production deploy state

### 2.1 Fly version matches HEAD

- **Check:** The image running on `quorus-relay.fly.dev` was built from the deploy commit.
- **Command:**
  ```bash
  flyctl status -a quorus-relay | grep -E 'Image|Updated'
  git rev-parse --short HEAD
  ```
- **Pass:** Fly image tag (or release SHA in `flyctl releases`) ends with the same 7-char SHA as HEAD.
- [ ]

### 2.2 Fly machine count is exactly 1 (autoscaling off)

- **Check:** Hard cap is one machine; autoscaling cannot fan out.
- **Command:**
  ```bash
  flyctl status -a quorus-relay | grep -c started
  ```
- **Pass:** Output is `1`.
- [ ]

### 2.3 Alembic head matches deployed migrations

- **Check:** Database schema is on the latest revision.
- **Command:**
  ```bash
  flyctl ssh console -a quorus-relay -C "alembic current"
  ```
- **Pass:** Output ends with `(head)`.
- [ ]

---

## 3. Vercel deploy state

### 3.1 Website is on latest commit

- **Check:** `quorus.dev` is serving the deploy commit.
- **Command:**
  ```bash
  curl -s https://www.quorus.dev/ | grep -oE 'data-build="[^"]+"' || curl -sI https://www.quorus.dev/ | grep -i 'x-vercel-id'
  vercel ls quorus | head -3
  ```
- **Pass:** Most recent Vercel deployment is `Ready` and points at the deploy commit SHA.
- [ ]

### 3.2 Asciinema cast loads

- **Check:** The hero demo cast is reachable.
- **Command:**
  ```bash
  curl -sI https://www.quorus.dev/casts/demo_reflex.cast | head -1
  ```
- **Pass:** `HTTP/2 200`.
- [ ]

### 3.3 Lighthouse SEO + a11y unchanged

- **Check:** Public pages didn't regress.
- **Command:**
  ```bash
  npx --yes lighthouse https://www.quorus.dev/ --only-categories=seo,accessibility --quiet --chrome-flags="--headless" --output=json | jq '.categories | {seo: .seo.score, a11y: .accessibility.score}'
  ```
- **Pass:** `seo >= 0.95`, `a11y >= 0.90`.
- [ ]

---

## 4. Secrets configured (Fly side)

### 4.1 RELAY_SECRET set (legacy admin path)

- **Check:** Bootstrap secret exists for admin tooling, not used as auth wedge in prod.
- **Command:**
  ```bash
  flyctl secrets list -a quorus-relay | grep -E '^RELAY_SECRET'
  ```
- **Pass:** Listed with non-empty digest. (`ALLOW_LEGACY_AUTH=false` is set in `fly.toml`.)
- [ ]

### 4.2 JWT_SECRET set (token signing)

- **Command:**
  ```bash
  flyctl secrets list -a quorus-relay | grep -E '^JWT_SECRET'
  ```
- **Pass:** Listed with non-empty digest.
- [ ]

### 4.3 DATABASE_URL set (Postgres)

- **Command:**
  ```bash
  flyctl secrets list -a quorus-relay | grep -E '^DATABASE_URL'
  ```
- **Pass:** Listed with non-empty digest.
- [ ]

### 4.4 SENTRY_DSN set (error capture)

- **Command:**
  ```bash
  flyctl secrets list -a quorus-relay | grep -E '^SENTRY_DSN'
  ```
- **Pass:** Listed with non-empty digest.
- [ ]

### 4.5 REDIS_URL set (rate limiting + presence)

- **Command:**
  ```bash
  flyctl secrets list -a quorus-relay | grep -E '^REDIS_URL'
  ```
- **Pass:** Listed with non-empty digest.
- [ ]

---

## 5. Health endpoints

### 5.1 Relay /health is 200

- **Command:**
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' https://quorus-relay.fly.dev/health
  ```
- **Pass:** `200`.
- [ ]

### 5.2 Relay /health/detailed shows DB+Redis green

- **Command:**
  ```bash
  curl -s https://quorus-relay.fly.dev/health/detailed | jq
  ```
- **Pass:** `db.ok = true`, `redis.ok = true`.
- [ ]

### 5.3 Website root is 200

- **Command:**
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' https://www.quorus.dev/
  ```
- **Pass:** `200`.
- [ ]

### 5.4 Asciinema cast renders in browser

- **Check:** Visual smoke — cast actually plays.
- **Command:**
  ```bash
  open https://www.quorus.dev/
  ```
- **Pass:** Hero terminal animates within 3 seconds, no spinner stuck.
- [ ]

---

## 6. Real-harness verification

### 6.1 Tier-A confirmed wired (Claude / Codex / Gemini)

- **Check:** Each harness has a verified production path.
- **Command:**
  ```bash
  pytest -v tests/test_reflexd_adapters.py -k 'claude or codex or gemini'
  ```
- **Pass:** All three suites green.
- [ ]

### 6.2 Tier-A pending (Cursor / Opencode / Cline)

- **Check:** Argv builders pinned, but live OAuth not exercised in CI yet.
- **Command:**
  ```bash
  pytest -v tests/test_reflexd_adapters.py -k 'cursor or opencode or cline'
  ```
- **Pass:** Argv shape tests green. Tracked in `docs/POST_LAUNCH_30DAY_PLAN.md` for live CI add.
- [ ]

### 6.3 Tier-B confirmed (Windsurf MCP-attached)

- **Check:** MCP server config writer works; reflexd does **not** try to wake.
- **Command:**
  ```bash
  pytest -v tests/test_mcp_writers.py -k windsurf
  ```
- **Pass:** Suite green; `_HARNESS_SUFFIXES` does not include `windsurf`.
- [ ]

---

## 7. Rate-limit smoke

### 7.1 register-agent caps at 10/hour

- **Check:** 11th call inside an hour returns 429.
- **Command:**
  ```bash
  PARENT_KEY="<parent-api-key>"; for i in $(seq 1 11); do \
    curl -s -o /dev/null -w "$i: %{http_code}\n" \
      -X POST https://quorus-relay.fly.dev/v1/auth/register-agent \
      -H "Authorization: Bearer $PARENT_KEY" \
      -H 'Content-Type: application/json' \
      -d "{\"suffix\":\"smoke$i\"}"; done
  ```
- **Pass:** Calls 1–10 return `200` or `409` (duplicate). Call 11 returns `429`.
- [ ]

---

## 8. GDPR endpoints

### 8.1 DELETE /v1/dm/{participant} purges + returns 200

- **Check:** Self-erasure works for the calling participant (not legacy bearer).
- **Command:**
  ```bash
  AGENT_KEY="<your-agent-key>"; AGENT_NAME="<your-agent-name>"; \
    curl -s -X DELETE -w '\n%{http_code}\n' \
      -H "Authorization: Bearer $AGENT_KEY" \
      "https://quorus-relay.fly.dev/v1/dm/$AGENT_NAME"
  ```
- **Pass:** Body is `{"purged_count": N, ...}`, status `200`. Legacy bearer rejected with `403`.
- [ ]

### 8.2 GDPR audit hook fires

- **Check:** Audit ledger captured the purge.
- **Command:**
  ```bash
  flyctl ssh console -a quorus-relay -C "psql \$DATABASE_URL -c \"SELECT count(*) FROM audit_log WHERE event_type='dm:agent:purge' AND created_at > now()-interval '1 hour';\""
  ```
- **Pass:** Count >= 1 (matches your test purge).
- [ ]

---

## 9. Backup posture

### 9.1 Last pg_dump within 24 hours

- **Command:**
  ```bash
  flyctl postgres backup list -a quorus-relay-db | head -3
  ```
- **Pass:** Most recent backup `Status = ready`, `created_at` < 24h ago.
- [ ]

### 9.2 Last test-restore within 30 days

- **Check:** Restore was actually exercised — backup that doesn't restore is not a backup.
- **Command:**
  ```bash
  cat ~/.quorus/ops/last_restore_test.txt
  ```
- **Pass:** ISO date < 30 days old. (Recurring monthly via the 30-day plan; first run is part of launch prep.)
- [ ]

### 9.3 RTO / RPO documented

- **Check:** Recovery Time Objective and Recovery Point Objective are committed to repo.
- **Pass criterion:** `docs/POST_DEPLOY_VERIFICATION.md` declares `RTO=15min, RPO=24h` and runbook (Section 11) covers Postgres-down. Tick when both are present.
- [ ]

---

## 10. Monitoring

### 10.1 Sentry receives a live test event

- **Check:** Manual ping reaches Sentry.
- **Command:**
  ```bash
  flyctl ssh console -a quorus-relay -C "python -c \"import sentry_sdk; sentry_sdk.capture_message('launch-readiness-ping')\""
  ```
- **Pass:** Within 60s the message appears in the Sentry project's Issues feed.
- [ ]

### 10.2 /metrics returns Prometheus counters

- **Command:**
  ```bash
  curl -s https://quorus-relay.fly.dev/metrics | grep -E '^http_requests_total|^quorus_' | head -5
  ```
- **Pass:** At least one `http_requests_total` and one `quorus_*` line.
- [ ]

### 10.3 Fly health-check alerts wired to email/Slack

- **Check:** Fly alert policy notifies on `/health` failure for >2 minutes.
- **Command:**
  ```bash
  flyctl alerts list -a quorus-relay 2>/dev/null || open https://fly.io/apps/quorus-relay/monitoring
  ```
- **Pass:** At least one active rule on `/health` 500s/timeouts. Email or webhook receiver set.
- [ ]

---

## 11. Incident-response runbooks

> **Where to find them:** under `docs/runbooks/` (one file per scenario). If a runbook is missing, **STOP** — write it before launching, even if 10 lines.

### 11.1 Upstash quota exhausted

- **Symptom:** `redis: max connections` in logs; rate limiter fails open.
- **Fix sequence:**
  1. `flyctl logs -a quorus-relay --since 5m | grep -i redis` — confirm.
  2. Open Upstash console → upgrade plan one tier (Free → Pro is one click).
  3. `flyctl restart -a quorus-relay` — pick up new connection budget.
  4. Watch /metrics: `quorus_rate_limit_redis_errors_total` should stop incrementing.
- **Owner:** Arav.
- [ ]

### 11.2 Postgres down

- **Symptom:** `/health/detailed` returns `db.ok=false`; relay logs `OperationalError`.
- **Fix sequence:**
  1. `flyctl postgres list` → confirm cluster status.
  2. `flyctl postgres restart -a quorus-relay-db` (or failover to standby if HA enabled).
  3. If unrecoverable: restore latest backup via `flyctl postgres backup restore <id>`.
  4. RTO target: 15 min. RPO: 24 h (last pg_dump).
  5. After restore: re-run section 8.2 audit query to confirm GDPR ledger intact.
- **Owner:** Arav.
- [ ]

### 11.3 OAuth on a harness expires

- **Symptom:** `reflexd` logs `claude --print` (or `codex exec` / `gemini --prompt`) returning `401 / login required`. No replies posted.
- **Fix sequence:**
  1. SSH to the host running reflexd.
  2. Re-auth: `claude /login` (or `codex login` / `gemini auth login` / `cursor-agent login` / `opencode auth login` / `cline auth`).
  3. `quorus reflexd-manager restart` — pick up the refreshed token.
  4. Test: post `@<agent> ping` in a room; expect a reply within 5 s.
- **Owner:** the human on whose machine reflexd runs.
- [ ]

---

## Final go/no-go

- [ ] All 30 boxes above ticked.
- [ ] `git status` clean on `feat/may4-sprint`.
- [ ] `CONTEXT.md` "In Progress" updated to "launching".
- [ ] Tweet thread queued (`docs/MARKETING_LAUNCH_TWEET.md`).
- [ ] 30-day plan loaded (`docs/POST_LAUNCH_30DAY_PLAN.md`).

If any line above is unticked at T-0, do **not** post the launch tweet. Fix first.
