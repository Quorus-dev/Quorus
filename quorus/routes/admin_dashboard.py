"""Admin dashboard — server-rendered HTML view of /admin/metrics.

Browser-friendly auth via a signed cookie (bearer headers don't survive
plain GETs). Login flow:

    GET  /admin/login    — single-field password form (RELAY_SECRET)
    POST /admin/login    — set HttpOnly cookie on success, rate-limited
    GET  /admin/dashboard — read cookie, render HTML; falls back to Bearer

Also accepts an `Authorization: Bearer <RELAY_SECRET>` header so the
dashboard remains scriptable (curl + watch, for instance).

The page fetches metrics in-process via `compute_metrics()` so there's no
second HTTP hop and no duplicate auth work.
"""

from __future__ import annotations

import hmac
import html
import os
from datetime import datetime, timezone

import jwt as jwt_lib
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.auth.tokens import _get_jwt_secret, decode_jwt  # noqa: F401
from quorus.routes.admin_metrics import compute_metrics

router = APIRouter(prefix="/admin", tags=["admin-dashboard"])

COOKIE_NAME = "quorus_admin"
COOKIE_TTL = 300  # seconds — login is cheap; keep tight
RATE_LIMIT_WINDOW = 600  # 10 min brute-force window
RATE_LIMIT_MAX = 10


# ---------------------------------------------------------------------------
# Dashboard-scoped JWT (avoid coupling admin session to participant schema)
# ---------------------------------------------------------------------------

def _mint_admin_cookie_token() -> str:
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "admin-dashboard",
        "role": "admin",
        "iss": "quorus",
        "aud": "quorus-admin-cookie",
        "iat": now,
        "exp": now + timedelta(seconds=COOKIE_TTL),
    }
    return jwt_lib.encode(payload, _get_jwt_secret(), algorithm="HS256")


def _verify_admin_cookie_token(token: str) -> bool:
    try:
        claims = jwt_lib.decode(
            token,
            _get_jwt_secret(),
            algorithms=["HS256"],
            issuer="quorus",
            audience="quorus-admin-cookie",
        )
    except jwt_lib.PyJWTError:
        return False
    return claims.get("role") == "admin"


def _relay_secret() -> str:
    return os.environ.get("RELAY_SECRET", "")


# ---------------------------------------------------------------------------
# Auth helper — cookie first, then Authorization header
# ---------------------------------------------------------------------------

def _is_admin_cookie(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    return bool(token) and _verify_admin_cookie_token(token)


def _is_admin_bearer(request: Request) -> bool:
    """Allow the existing Bearer-admin path (RELAY_SECRET or JWT role=admin)."""
    try:
        import asyncio

        auth: AuthContext = asyncio.get_event_loop().run_until_complete(
            verify_auth(request)
        )
        return auth.role == "admin" or auth.is_legacy
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

_LOGIN_FORM_HTML = """
<!DOCTYPE html>
<html class="bg-[#08080f] text-[#f0ede8]">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>quorus · admin</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Geist', ui-sans-serif, system-ui, -apple-system, sans-serif; }}
  </style>
</head>
<body class="min-h-screen flex items-center justify-center px-6">
  <form method="post" action="/admin/login"
        class="w-full max-w-sm rounded-2xl border border-teal-500/20 bg-white/[0.03] p-8 backdrop-blur">
    <div class="mb-6 text-center">
      <div class="text-2xl font-bold text-teal-400">quorus</div>
      <div class="text-xs font-mono text-white/45 tracking-widest uppercase mt-1">
        admin dashboard
      </div>
    </div>
    {error}
    <label class="block text-xs uppercase tracking-widest text-white/55 mb-2 font-mono">
      Relay secret
    </label>
    <input type="password" name="secret" autofocus required
           class="w-full rounded-xl border border-white/10 bg-[#08080f] px-4 py-3 text-sm
                  text-white placeholder-white/30 font-mono
                  focus:outline-none focus:ring-2 focus:ring-teal-500/50 focus:border-teal-500/30"
           placeholder="RELAY_SECRET"/>
    <button type="submit"
            class="w-full mt-5 rounded-xl bg-teal-500 hover:bg-teal-400 text-black
                   text-sm font-semibold py-3 transition-colors">
      Sign in
    </button>
    <p class="mt-5 text-[11px] text-white/40 font-mono text-center leading-relaxed">
      Admin-only access. All requests are rate-limited.
    </p>
  </form>
</body>
</html>
""".strip()


@router.get("/login", response_class=HTMLResponse)
async def admin_login_form(request: Request, error: str | None = None):
    err_block = ""
    if error:
        err_block = (
            f'<div class="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 '
            f'px-3 py-2 text-xs text-red-300">{html.escape(error)}</div>'
        )
    return HTMLResponse(_LOGIN_FORM_HTML.format(error=err_block))


@router.post("/login")
async def admin_login_submit(
    request: Request,
    secret: str = Form(...),
):
    client_ip = request.client.host if request.client else "unknown"
    rate_svc = request.app.state.rate_limit_service
    allowed = await rate_svc.check_with_limit(
        "global", f"admin-login:{client_ip}", RATE_LIMIT_MAX, window=RATE_LIMIT_WINDOW,
    )
    if not allowed:
        return RedirectResponse(
            url="/admin/login?error=Too+many+attempts.+Try+again+later.",
            status_code=303,
        )

    expected = _relay_secret()
    if not expected or not hmac.compare_digest(secret, expected):
        return RedirectResponse(
            url="/admin/login?error=Invalid+secret.", status_code=303,
        )

    token = _mint_admin_cookie_token()
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_TTL,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/admin",
    )
    return response


@router.post("/logout")
async def admin_logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/admin")
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _cookie_or_bearer_admin(request: Request) -> bool:
    """Accept either a signed admin cookie or a RELAY_SECRET Bearer header."""
    if _is_admin_cookie(request):
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        secret = _relay_secret()
        if secret and hmac.compare_digest(token, secret):
            return True
    return False


def _render_sparkline(per_day: list[dict]) -> str:
    """Inline SVG polyline. Clamps everything to a fixed viewBox."""
    if not per_day:
        return '<div class="text-xs text-white/40 font-mono">no data yet</div>'
    counts = [item["count"] for item in per_day]
    max_c = max(counts) or 1
    w, h, pad = 640, 120, 10
    step = (w - pad * 2) / max(1, len(counts) - 1)
    points = [
        f"{pad + i * step:.1f},{h - pad - (c / max_c) * (h - pad * 2):.1f}"
        for i, c in enumerate(counts)
    ]
    polyline = " ".join(points)
    first_date = html.escape(per_day[0]["date"])
    last_date = html.escape(per_day[-1]["date"])
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
        f'class="w-full h-28 overflow-visible">'
        f'<polyline fill="none" stroke="#14b8a6" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" points="{polyline}"/>'
        f'</svg>'
        f'<div class="flex justify-between text-[10px] font-mono text-white/35 mt-2">'
        f'<span>{first_date}</span><span>peak: {max_c}</span><span>{last_date}</span>'
        f'</div>'
    )


def _render_stat_card(label: str, value: str, sub: str | None = None) -> str:
    sub_html = (
        f'<div class="mt-2 text-[11px] font-mono text-teal-300/70">{html.escape(sub)}</div>'
        if sub else ""
    )
    return f"""
    <div class="rounded-2xl border border-teal-500/20 bg-white/[0.03] p-5 backdrop-blur">
      <div class="text-[11px] font-mono text-white/45 tracking-widest uppercase">{html.escape(label)}</div>
      <div class="mt-2 text-3xl font-bold text-white tabular-nums">{html.escape(value)}</div>
      {sub_html}
    </div>
    """


def _render_top_table(rows: list[dict]) -> str:
    if not rows:
        return (
            '<div class="p-6 text-xs text-white/40 font-mono">'
            'no workspaces yet</div>'
        )
    body = "\n".join(
        f"<tr class='border-t border-white/5 hover:bg-white/[0.02] transition-colors'>"
        f"<td class='px-4 py-3 font-mono text-sm text-teal-300'>{html.escape(r['slug'])}</td>"
        f"<td class='px-4 py-3 text-sm text-white/75'>{html.escape(r.get('display_name') or '—')}</td>"
        f"<td class='px-4 py-3 text-sm text-white tabular-nums text-right'>{int(r['msgs_30d']):,}</td>"
        f"<td class='px-4 py-3 text-[11px] font-mono text-white/45 text-right'>"
        f"{html.escape(_humanize_time(r.get('last_active_at')))}</td>"
        f"</tr>"
        for r in rows
    )
    return f"""
    <table class="w-full">
      <thead>
        <tr class="text-[11px] font-mono text-white/45 tracking-widest uppercase">
          <th class="px-4 py-3 text-left">slug</th>
          <th class="px-4 py-3 text-left">name</th>
          <th class="px-4 py-3 text-right">msgs · 30d</th>
          <th class="px-4 py-3 text-right">last active</th>
        </tr>
      </thead>
      <tbody>{body}</tbody>
    </table>
    """


def _humanize_time(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        ts = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return "—"
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html class="bg-[#08080f] text-[#f0ede8]">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>quorus · analytics</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Geist', ui-sans-serif, system-ui, sans-serif; }}
  </style>
</head>
<body>
  <header class="border-b border-white/5">
    <div class="max-w-6xl mx-auto px-6 py-5 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="text-xl font-bold text-teal-400">quorus</div>
        <span class="text-xs font-mono text-white/45 tracking-widest uppercase">relay analytics</span>
      </div>
      <div class="flex items-center gap-4 text-[11px] font-mono text-white/45">
        <span>updated {generated}</span>
        <form method="post" action="/admin/logout">
          <button class="px-3 py-1.5 rounded-lg border border-white/10 hover:border-teal-500/40 hover:text-teal-300 transition-colors">
            sign out
          </button>
        </form>
      </div>
    </div>
  </header>
  <main class="max-w-6xl mx-auto px-6 py-10 space-y-6">
    {limited_banner}
    <section class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {card_workspaces}
      {card_dau}
      {card_wau}
      {card_mau}
    </section>

    <section class="rounded-2xl border border-teal-500/20 bg-white/[0.03] p-6 backdrop-blur">
      <div class="flex items-baseline justify-between mb-4">
        <div>
          <div class="text-[11px] font-mono text-white/45 tracking-widest uppercase">messages · last 30 days</div>
          <div class="mt-2 text-3xl font-bold text-white tabular-nums">{total_30d:,}</div>
        </div>
      </div>
      {sparkline}
    </section>

    <section class="rounded-2xl border border-teal-500/20 bg-white/[0.03] overflow-hidden backdrop-blur">
      <div class="px-4 py-4 border-b border-white/5">
        <div class="text-[11px] font-mono text-white/45 tracking-widest uppercase">top workspaces · 30d</div>
      </div>
      <div class="overflow-x-auto">
        {top_table}
      </div>
    </section>
  </main>

  <footer class="max-w-6xl mx-auto px-6 py-8 text-[11px] font-mono text-white/30">
    <span>quorus · admin</span>
    <span class="mx-2 text-white/15">·</span>
    <a href="https://quorus.dev" class="hover:text-teal-400">quorus.dev</a>
  </footer>
</body>
</html>
""".strip()


@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not _cookie_or_bearer_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    metrics = await compute_metrics(request, days=30, top=10)
    mode = metrics.get("mode", "limited")

    if mode == "postgres":
        card_workspaces = _render_stat_card(
            "workspaces",
            str(metrics.get("total_workspaces") or 0),
            sub=f"+{(metrics.get('new_workspaces') or {}).get('7d', 0)} in 7d",
        )
        ws_au = metrics.get("active_users") or {}
        card_dau = _render_stat_card("DAU", str(ws_au.get("dau", 0)))
        card_wau = _render_stat_card("WAU", str(ws_au.get("wau", 0)))
        card_mau = _render_stat_card("MAU", str(ws_au.get("mau", 0)))
        limited_banner = ""
    else:
        card_workspaces = _render_stat_card("workspaces", "—", sub="Postgres not configured")
        card_dau = _render_stat_card("DAU", "—")
        card_wau = _render_stat_card("WAU", "—")
        card_mau = _render_stat_card("MAU", "—")
        note = metrics.get("note", "")
        limited_banner = (
            '<div class="rounded-2xl border border-amber-500/30 bg-amber-500/[0.06] '
            'p-4 text-xs font-mono text-amber-300">'
            f'{html.escape(note)}'
            '</div>'
        )

    messages = metrics.get("messages") or {}
    total_30d = int(messages.get("total_30d") or 0)
    sparkline = _render_sparkline(messages.get("per_day") or [])
    top_table = _render_top_table(metrics.get("top_workspaces") or [])

    generated = metrics.get("generated_at", "")
    # Render short timestamp in header
    generated_short = generated[:16].replace("T", " ") if generated else ""

    html_out = _DASHBOARD_TEMPLATE.format(
        generated=html.escape(generated_short),
        limited_banner=limited_banner,
        card_workspaces=card_workspaces,
        card_dau=card_dau,
        card_wau=card_wau,
        card_mau=card_mau,
        total_30d=total_30d,
        sparkline=sparkline,
        top_table=top_table,
    )
    return HTMLResponse(html_out)


# ---------------------------------------------------------------------------
# Unused; keeps interface symmetry for tests
# ---------------------------------------------------------------------------

def _assert_auth_ok(request: Request) -> None:
    if not _cookie_or_bearer_admin(request):
        raise HTTPException(status_code=401, detail="admin auth required")


# Suppress "imported but unused" complaints on Response / Depends
_ = (Response, Depends)
