"""`quorus invites`: the relay owner's invites, and `--revoke <id>` to kill one.

Shared-secret relays only (docs/design/invite-roles.md, step 1). The relay
never returns a code, only its id, room, uses and expiry. Revoking stops new
joins with that code; people who already joined keep their own keys.
"""

from __future__ import annotations

import time

import httpx


def _fmt_expiry(expires_at: float, now: float) -> str:
    left = expires_at - now
    if left <= 0:
        return "expired"
    if left < 3600:
        return f"{int(left // 60)}m left"
    if left < 86400:
        return f"{int(left // 3600)}h left"
    return f"{int(left // 86400)}d left"


def _status(inv: dict, now: float) -> str:
    if inv.get("revoked"):
        return "revoked"
    if inv.get("uses", 0) >= inv.get("max_uses", 1):
        return "used up"
    return _fmt_expiry(float(inv.get("expires_at") or 0), now)


def cmd_invites(args) -> int:
    from quorus_cli import cli, ui

    if not cli.RELAY_URL:
        ui.error("Relay URL not configured", hint="run: quorus init <name> --secret -")
        return 1
    base = f"{cli.RELAY_URL.rstrip('/')}/v1/join/invites"
    revoke = getattr(args, "revoke", None)
    try:
        if revoke:
            resp = httpx.post(f"{base}/{revoke}/revoke", headers=cli._auth_headers(),
                              timeout=15, follow_redirects=False)
        else:
            resp = httpx.get(base, headers=cli._auth_headers(), timeout=15,
                             follow_redirects=False)
    except httpx.HTTPError:
        ui.error_with_retry("Can't reach relay", relay_url=cli.RELAY_URL)
        return 2
    if resp.status_code in (401, 403):
        ui.error("Only the relay owner manages invites",
                 hint="run this on the machine that holds the relay secret")
        return 3
    if resp.status_code == 404:
        ui.error("No such invite" if revoke else "This relay doesn't use invites",
                 hint="list them: quorus invites" if revoke else
                 "API-key relays use invite tokens; see quorus share")
        return 4
    if resp.status_code == 400:
        ui.error("That isn't an invite id", hint="ids look like inv_0123456789ab")
        return 4
    if resp.status_code != 200:
        ui.error(f"Relay answered HTTP {resp.status_code}")
        return 1
    if revoke:
        removed = (resp.json() or {}).get("removed") or []
        who = (f"removed from its room: {', '.join('@' + n for n in removed)}"
               if removed else "no one who joined with it is still in its room")
        ui.console.print(f"[green]Revoked {revoke}[/green]: its code admits no one new, "
                         f"and {who}.")
        return 0
    invites = resp.json() or []
    if not invites:
        ui.console.print("No invites yet. Make one: quorus share <room>")
        return 0
    now = time.time()
    for inv in invites:
        # Observer invites are read-only; say so, or they look like any other.
        role = "  read-only" if inv.get("role") == "observer" else ""
        ui.console.print(f"  {inv['id']}  #{inv['room']}{role}  {inv['uses']}/{inv['max_uses']} "
                         f"used  {_status(inv, now)}  by {inv['issuer']}")
    ui.console.print("\n  [dim]revoke one: quorus invites --revoke <id>[/dim]")
    return 0
