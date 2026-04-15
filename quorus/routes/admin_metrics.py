"""Admin analytics endpoint — global usage signal for the founders.

Exposes:
    GET /admin/metrics   — JSON stats (auth: admin role, or legacy secret).
    compute_metrics()    — reusable coroutine so the HTML dashboard route
                           can call it in-process without a second HTTP hop.

Two modes:
    - "postgres": full stats from tenants + audit_ledger.
    - "limited": fallback when DATABASE_URL is unset (dev / ephemeral).
      Returns only the pieces derivable from the in-memory AnalyticsService
      (messages per day bucketed from hourly volume).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from quorus.auth.middleware import AuthContext, require_role, verify_auth

router = APIRouter(prefix="/admin", tags=["admin-metrics"])


# ---------------------------------------------------------------------------
# SQL — centralized so they're greppable + reviewable in one place.
# ---------------------------------------------------------------------------

_SQL_TOTAL_WORKSPACES = "SELECT count(*) FROM tenants"

_SQL_NEW_WORKSPACES = """
SELECT
  count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS h24,
  count(*) FILTER (WHERE created_at >= now() - interval '7 days')   AS d7,
  count(*) FILTER (WHERE created_at >= now() - interval '30 days')  AS d30
FROM tenants
"""

_SQL_ACTIVE_USERS = """
SELECT
  count(DISTINCT actor) FILTER (WHERE created_at >= now() - interval '1 day')  AS dau,
  count(DISTINCT actor) FILTER (WHERE created_at >= now() - interval '7 days') AS wau,
  count(DISTINCT actor) FILTER (WHERE created_at >= now() - interval '30 days') AS mau
FROM audit_ledger
WHERE event_type = 'message_created' AND actor IS NOT NULL
"""

_SQL_MESSAGES_PER_DAY = """
SELECT date_trunc('day', created_at)::date AS day, count(*) AS c
FROM audit_ledger
WHERE event_type = 'message_created'
  AND created_at >= now() - make_interval(days => :days)
GROUP BY 1
ORDER BY 1
"""

# Join on t.id = a.tenant_id::text handles the schema skew between tenants.id
# (varchar(36)) and audit_ledger.tenant_id (varchar(64)) without casting.
_SQL_TOP_WORKSPACES = """
WITH agg AS (
  SELECT tenant_id, count(*) AS msgs, max(created_at) AS last_active_at
  FROM audit_ledger
  WHERE event_type = 'message_created'
    AND created_at >= now() - interval '30 days'
  GROUP BY tenant_id
  ORDER BY msgs DESC
  LIMIT :top
)
SELECT t.id, t.slug, t.display_name, a.msgs, a.last_active_at
FROM agg a JOIN tenants t ON t.id = a.tenant_id
ORDER BY a.msgs DESC
"""


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

async def _compute_postgres(days: int, top: int) -> dict:
    """Full Postgres-backed metrics."""
    from sqlalchemy import text

    from quorus.storage.postgres import get_db_session

    async with get_db_session() as session:
        total_row = await session.execute(text(_SQL_TOTAL_WORKSPACES))
        total = total_row.scalar_one() or 0

        nw_row = (await session.execute(text(_SQL_NEW_WORKSPACES))).one()
        new_workspaces = {
            "24h": int(nw_row.h24 or 0),
            "7d": int(nw_row.d7 or 0),
            "30d": int(nw_row.d30 or 0),
        }

        au_row = (await session.execute(text(_SQL_ACTIVE_USERS))).one()
        active_users = {
            "dau": int(au_row.dau or 0),
            "wau": int(au_row.wau or 0),
            "mau": int(au_row.mau or 0),
        }

        per_day_rows = (
            await session.execute(text(_SQL_MESSAGES_PER_DAY), {"days": days})
        ).all()
        per_day_map = {row.day.isoformat(): int(row.c) for row in per_day_rows}
        per_day = _zero_fill_per_day(per_day_map, days)
        total_30d = sum(item["count"] for item in per_day)

        top_rows = (
            await session.execute(text(_SQL_TOP_WORKSPACES), {"top": top})
        ).all()
        top_workspaces = [
            {
                "tenant_id": str(row.id),
                "slug": row.slug,
                "display_name": row.display_name or row.slug,
                "msgs_30d": int(row.msgs),
                "last_active_at": row.last_active_at.isoformat()
                if row.last_active_at
                else None,
            }
            for row in top_rows
        ]

    return {
        "mode": "postgres",
        "generated_at": _utcnow_iso(),
        "total_workspaces": int(total),
        "new_workspaces": new_workspaces,
        "active_users": active_users,
        "messages": {
            "total_30d": total_30d,
            "per_day": per_day,
        },
        "top_workspaces": top_workspaces,
    }


async def _compute_limited(request: Request, days: int) -> dict:
    """Fallback when no DATABASE_URL — rebuild per-day from in-memory analytics."""
    analytics_svc = request.app.state.analytics_service
    # Aggregate across tenants since limited mode is usually single-tenant dev.
    stats = await analytics_svc.get_stats("_legacy")
    hourly = stats.get("hourly_volume", {}) or {}

    day_bucket: dict[str, int] = {}
    for hour_key, count in hourly.items():
        # hour_key like "2026-04-15T14" — truncate to date.
        day = hour_key.split("T", 1)[0]
        day_bucket[day] = day_bucket.get(day, 0) + int(count)
    per_day = _zero_fill_per_day(day_bucket, days)
    total_30d = sum(item["count"] for item in per_day)

    return {
        "mode": "limited",
        "generated_at": _utcnow_iso(),
        "total_workspaces": None,
        "new_workspaces": None,
        "active_users": None,
        "messages": {
            "total_30d": total_30d,
            "per_day": per_day,
        },
        "top_workspaces": [],
        "note": (
            "DATABASE_URL is not set — running in limited mode. Workspace, "
            "signup, and DAU/WAU/MAU metrics require Postgres-backed storage."
        ),
    }


async def compute_metrics(
    request: Request,
    *,
    days: int = 30,
    top: int = 10,
) -> dict:
    """Return the full metrics payload. Picks Postgres vs limited mode.

    Kept as a module-level coroutine so the HTML dashboard route can invoke
    it in-process without making a second authenticated HTTP call to itself.
    """
    days = max(1, min(days, 90))
    top = max(1, min(top, 50))

    if os.environ.get("DATABASE_URL"):
        try:
            return await _compute_postgres(days, top)
        except Exception:
            # Fall through to limited if Postgres errors — don't 500 the
            # dashboard just because audit_ledger is momentarily unhappy.
            pass
    return await _compute_limited(request, days)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _zero_fill_per_day(bucket: dict[str, int], days: int) -> list[dict]:
    """Return exactly `days` items ending today. Missing days count as 0."""
    today = datetime.now(timezone.utc).date()
    result = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        result.append({"date": d, "count": int(bucket.get(d, 0))})
    return result


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def get_admin_metrics(
    request: Request,
    days: int = Query(30, ge=1, le=90),
    top: int = Query(10, ge=1, le=50),
    auth: AuthContext = Depends(verify_auth),
):
    """Admin-only global analytics. Legacy RELAY_SECRET is treated as admin."""
    try:
        require_role(auth, "admin")
    except HTTPException:
        raise
    # Small in-process cache to keep dashboard refresh responsive.
    cache = request.app.state.__dict__.setdefault("_metrics_cache", {})
    key = (days, top)
    now = time.monotonic()
    entry = cache.get(key)
    if entry and now - entry[0] < 30:
        return entry[1]
    data = await compute_metrics(request, days=days, top=top)
    cache[key] = (now, data)
    return data
