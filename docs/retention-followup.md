# Retention metrics — follow-up

The admin dashboard at `/admin/dashboard` and `GET /admin/metrics` currently
report total workspaces, new signups by window, DAU/WAU/MAU, messages per
day, and top workspaces. Cohort retention (D1 / D7 / D30) is intentionally
deferred — this doc captures why and the work needed to add it cleanly.

## Why it wasn't shipped in v1

Retention is `|cohort ∩ active_on_day_N| / |cohort|`. Computing it needs
two things we don't have today:

1. **A stable cohort key per participant.** `audit_ledger` records
   `actor` as free-text (the display name), not `participant_id`. Name
   changes therefore drift the cohort membership over time.
2. **A signup-date index.** `tenants.created_at` is the right anchor for
   workspace-cohort retention, but we have no table joining tenants to
   participants with an "active on day N" bit. Deriving it on every
   dashboard request would scan the whole audit ledger.

Computing retention at request time would also make the dashboard
unacceptably slow once the audit table grows past a few million rows.

## Plan

### 1. New `cohorts` table

```
cohorts
  participant_id UUID PRIMARY KEY
  tenant_id      TEXT NOT NULL
  signup_date    DATE NOT NULL                 -- truncated to day UTC
  cohort_week    DATE NOT NULL                 -- Monday of signup week
```

Populate at two points:

- **On signup.** `/v1/auth/signup` handler (in `quorus/auth/routes.py`)
  writes a `cohorts` row in the same transaction that creates the
  tenant + participant + API key.
- **Backfill script.** `scripts/backfill_cohorts.py` walks `participants`
  - `tenants` for historical rows and inserts missing cohort entries.

### 2. New `retention_snapshots` table

Pre-computed nightly; reading is cheap:

```
retention_snapshots
  snapshot_date   DATE NOT NULL
  cohort_date     DATE NOT NULL        -- 1 row per cohort per snapshot day
  cohort_size     INT  NOT NULL
  active_d1       INT
  active_d7       INT
  active_d30      INT
  PRIMARY KEY (snapshot_date, cohort_date)
```

### 3. Nightly job

`scripts/compute_retention.py`, scheduled via cron / Render cron / Fly
machine schedule. For each cohort in the last 90 days, counts
participants who produced any `MESSAGE_CREATED` audit event in the
D+1 / D+7 / D+30 windows, writes a snapshot row.

### 4. Dashboard surface

Extend `/admin/metrics` with `?include=retention`:

```json
{
  "retention": {
    "latest_snapshot": "2026-05-03",
    "cohorts": [
      { "cohort": "2026-04-01", "size": 42, "d1": 19, "d7": 11, "d30": 6 }
    ]
  }
}
```

Render as a simple cohort-by-row table in `admin_dashboard.py`,
conditionally hidden in limited mode.

### 5. CLI

Add `quorus stats --include retention` flag so founders can see the
same breakdown in the terminal without loading the web dashboard.

## Non-goals for v1 of retention

- Real-time retention (updates mid-day). Nightly is enough signal.
- Participant-level retention inside a single workspace. Workspace
  cohort retention is the load-bearing number for adoption.
- Funnel analysis (activation → paid). Out of scope until pricing exists.

## Effort estimate

~2 days of focused work once prioritized: migration + signup hook +
backfill script + nightly job + dashboard wiring + tests + one CLI flag.
Defer until there's ~100 weekly-active workspaces so the signal is real.
