# Sentry Setup — Production Relay

> Five-minute setup. Run once. Re-run only if rotating the DSN.
> Companion to `docs/OPERATIONS.md` (which explains the runtime behavior). This doc is the one-time wiring procedure.

---

## What you need

- Sentry account (free tier is fine — ~5K errors / 10K traces per month)
- `flyctl` CLI installed and authenticated against `quorus-relay`
- Repo checked out at the deploy commit

## Procedure

### 1. Create the Sentry project

1. Go to <https://sentry.io/signup> (skip if you already have an org).
2. **Projects → Create Project**. Pick the **FastAPI** platform; that pre-fills the right onboarding hints. The DSN format is identical regardless.
3. Name the project `quorus-relay`. Single environment slot is fine; tags handle env separation.

### 2. Copy the DSN

Settings → Projects → quorus-relay → Client Keys (DSN). Copy the URL. It looks like:

```
https://<public-key>@oXXXX.ingest.sentry.io/<project-id>
```

### 3. Set the secret on Fly

```sh
flyctl secrets set SENTRY_DSN='https://...@oXXXX.ingest.sentry.io/...' -a quorus-relay
```

Wrap the DSN in single quotes — the `@` and `/` will not survive an unquoted shell paste.

### 4. Redeploy so the new secret is loaded

```sh
flyctl deploy --remote-only -a quorus-relay
```

`release_command = "alembic upgrade head"` runs first; the deploy promotes only if migrations succeed.

### 5. Trigger a test event

```sh
curl -i https://quorus-relay.fly.dev/v1/this-route-does-not-exist
```

You should get back a 404. The 404 itself is intentionally **not** captured (4xx HTTPException is on the SDK ignore list — see `quorus/observability/sentry.py`). To force a real event, hit a route under temporary load with a known-bad payload, or temporarily uncomment a `raise RuntimeError("sentry smoke test")` in a dev-only handler and re-deploy.

Alternatively, the cleanest smoke test is to verify Sentry initialized at boot:

```sh
flyctl logs -a quorus-relay | grep "sentry init complete"
```

You should see one line per machine: `sentry init complete env=relay release=v<N>`.

### 6. Verify in the Sentry dashboard

Go to **Issues** in the Sentry UI. Within 30 seconds of any captured event, it should appear. Confirm:

- **Events appearing** — at least the boot event (or your test event) is present.
- **Scrubbing working** — open the event, scroll to "Request" → no `data` field, no `cookies`, no `Authorization` header. Open "Breadcrumbs" → any line that referenced `from_name` or `@<handle>` should read `[scrubbed-identity]`.
- **Release tag matches Fly version** — the issue should show `release: v<N>` matching the output of `flyctl releases list -a quorus-relay | head -2`.

If any of those three checks fail, do not consider Sentry shipped. Most common miss: scrubbing fails because someone added a new request body field — patch `_scrub_event` in `quorus/observability/sentry.py` and re-deploy.

---

## Tuning

Defaults are conservative. Dial these only with cause.

| Knob                        | Default | Bump-up trigger                                                 |
| --------------------------- | ------- | --------------------------------------------------------------- |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.05`  | Investigating a perf regression. Bump to `0.20` for a few days. |
| `profiles_sample_rate`      | `0.0`   | Don't change. Profiling needs the paid plan.                    |

Bump like this:

```sh
flyctl secrets set SENTRY_TRACES_SAMPLE_RATE=0.20 -a quorus-relay
flyctl deploy --remote-only -a quorus-relay
```

Drop back to `0.05` once the investigation closes — at higher sample rates the free tier exhausts in days.

---

## Disabling temporarily

```sh
flyctl secrets unset SENTRY_DSN -a quorus-relay
flyctl deploy --remote-only -a quorus-relay
```

`init_sentry()` becomes a no-op. Nothing else changes. Re-enable by re-running step 3.

---

## Rotating the DSN

If the DSN is exposed (logged, committed, leaked):

1. In Sentry → Settings → Client Keys → revoke the leaked key, create a new one.
2. Re-run step 3 with the new DSN.
3. Re-deploy (step 4).
4. Verify (step 6) that new events tag with the new project ID, then delete the old key entirely.
