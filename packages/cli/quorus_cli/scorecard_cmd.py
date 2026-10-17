"""`quorus scorecard`: which agent and vendor's work survives in this repo.

Reads the outcome ledger (quorus/ledger.py): provenance notes the daemon
attaches at publish, plus the room's review verdicts. Older commits are
matched to the room's publish lines and marked inferred.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from quorus import ledger
from quorus.config import load_config


def _history(room: str) -> list[dict[str, Any]]:
    from quorus_cli.agent_cmd import _bearer

    cfg = load_config()
    legacy = bool(cfg.get("relay_secret")) and not cfg.get("api_key")
    cred = cfg.get("relay_secret") if legacy else cfg.get("api_key")
    if not cred:
        return []
    try:
        headers = {"Authorization": f"Bearer {_bearer(cfg, cred, legacy)}"}
        resp = httpx.get(f"{cfg['relay_url'].rstrip('/')}/rooms/{room}/history",
                         params={"limit": 1000}, headers=headers, timeout=15)
        data = resp.json() if resp.status_code == 200 else []
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    return data if isinstance(data, list) else []


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%" if d else "-"


def cmd_scorecard(args: Any, console: Any) -> None:
    repo = Path(args.repo or ".").expanduser().resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"scorecard: not a git repo: {repo}")
    rev_range = args.range or f"--since={args.since}"
    history = _history(args.room) if args.room else []
    rows = ledger.outcomes(repo, rev_range, history)
    if args.json:
        console.print(ledger.to_json(rows), markup=False, highlight=False)
        return
    if not rows:
        console.print(f"[dim]no commits in {rev_range} on {repo.name}[/]")
        return
    from rich.table import Table

    by = args.by
    table = Table(title=f"{repo.name}: whose work holds up ({len(rows)} commits)",
                  title_justify="left", header_style="bold")
    for col in (by, "commits", "tests", "approved", "flagged", "reverted", "kept", "duels"):
        table.add_column(col, justify="left" if col == by else "right", no_wrap=True)
    for r in ledger.scorecard(rows, key=by):
        surv = "-" if r["survival"] is None else f"{round(100 * r['survival'])}%"
        duels = (f"{r['duels_won']}-{r['duels_lost']}"
                 if r["duels_won"] or r["duels_lost"] else "-")
        table.add_row(r[by], str(r["commits"]), _pct(r["with_tests"], r["commits"]),
                      str(r["approved"]), str(r["flagged"]), str(r["reverted"]), surv, duels)
    console.print(table)
    inferred = sum(o.inferred for o in rows)
    console.print("[dim]kept = share of the lines it added still in the code at HEAD"
                  + (f"; {inferred} commits attributed from the room's publish lines "
                     "(made before provenance notes)" if inferred else "") + "[/]")
    flagged = [o for o in rows if o.review == "flagged" or o.reverted]
    if flagged:
        console.print("\n[warning]Needs a look:[/]")
        for o in flagged[:8]:
            why = "reverted" if o.reverted else f"flagged by {', '.join(o.reviewers)}"
            console.print(f"  {o.sha[:8]} @{o.agent}  {o.subject[:60]}  [dim]({why})[/]")
    if not args.room:
        console.print("\n[dim]add --room <room> to include teammates' review verdicts[/]")
