# Operations

Runbook for the Quorus relay (`quorus-relay.fly.dev`).

## Error tracking (Sentry)

Production errors flow to Sentry for aggregation and alerting. Locally and in
CI the integration is a no-op — `init_sentry()` returns `False` without a DSN
so dev logs stay clean.

### One-time setup

1. Create a Sentry project at https://sentry.io (free tier is fine to start —
   ~5K errors / 10K traces per month). Pick the **FastAPI** platform when
   prompted; that pre-fills the right onboarding hints, but the DSN format is
   the same regardless.
2. Copy the DSN from **Settings → Projects → quorus-relay → Client Keys (DSN)**.
   It looks like `https://<public-key>@oXXXX.ingest.sentry.io/<project-id>`.
3. Set the secret on Fly:

   ```sh
   flyctl secrets set SENTRY_DSN=https://...@oXXXX.ingest.sentry.io/... -a quorus-relay
   ```

4. Trigger a redeploy so the new secret is loaded:

   ```sh
   flyctl deploy --remote-only -a quorus-relay
   ```

5. Verify by tailing logs:

   ```sh
   flyctl logs -a quorus-relay | grep "sentry init complete"
   ```

   You should see one line per machine: `sentry init complete env=relay release=<fly-version>`.

6. Smoke-test by hitting a known-bad path or temporarily deploying a route
   that raises — check the Sentry **Issues** tab; the event should land in
   under 30 seconds.

### What gets scrubbed before send

The `before_send` hook in `quorus/observability/sentry.py` strips:

- Request bodies (`request.data`) — these contain message content / room state.
- Request cookies and `Authorization` / `Cookie` headers — JWTs and session keys.
- Breadcrumbs that mention `from_name` or `@<participant>` — replaced with
  `[scrubbed-identity]`. Other breadcrumbs pass through untouched.

`send_default_pii=False` is also set as defense in depth so Sentry's own
auto-PII collectors are off.

### Release tagging

Each deploy tags events with the release version:

- On Fly: `FLY_RELEASE_VERSION` (auto-injected) → `release=v123` etc.
- Otherwise: `GIT_SHA` env var, or `dev` as a last resort.

To rollback to a specific release in Sentry, filter the Issues view by
`release:vN`. To compare error rates pre/post deploy, switch the **Releases**
tab to side-by-side mode.

### Sampling

`traces_sample_rate=0.05` (5%) is the default. At ~10K req/day that keeps
the free tier alive comfortably. To dial up:

```sh
flyctl secrets set SENTRY_TRACES_SAMPLE_RATE=0.20 -a quorus-relay
flyctl deploy --remote-only -a quorus-relay
```

`profiles_sample_rate` is hard-coded to `0.0` — profiling requires the paid
plan and isn't worth the spend until we hit consistent CPU regressions.

### What is NOT captured

- 4xx `HTTPException` (normal client errors) — too noisy, ignored at the SDK level.
- `asyncio.CancelledError` — fires on every clean SSE disconnect.
- Anything thrown before `lifespan` runs (e.g. config validation in module scope) —
  those crash the process at boot and Fly retries surface them in `flyctl logs`.

### Disabling temporarily

```sh
flyctl secrets unset SENTRY_DSN -a quorus-relay
flyctl deploy --remote-only -a quorus-relay
```

`init_sentry()` becomes a no-op; nothing else changes.
