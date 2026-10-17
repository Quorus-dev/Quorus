"""`quorus retro <room>`: what's going wrong in this room, ranked, with fixes."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from quorus import retro
from quorus_cli.scorecard_cmd import _history


def _since(history: list[dict[str, Any]], hours: float) -> list[dict[str, Any]]:
    cutoff = time.time() - hours * 3600
    out = []
    for m in history:
        try:
            t = datetime.fromisoformat(str(m.get("timestamp")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp() >= cutoff:
            out.append(m)
    return out


def cmd_retro(args: Any, console: Any) -> None:
    history = _since(_history(args.room), args.since)
    findings = retro.analyze(history)
    if not findings:
        console.print(f"[success]✓[/] #{args.room}: nothing stands out in the last "
                      f"{args.since:g}h ({len(history)} messages)")
        return
    console.print(f"[bold]#{args.room}: retro, last {args.since:g}h[/] "
                  f"[dim]({len(history)} messages)[/]")
    for i, f in enumerate(findings[:args.top], 1):
        console.print(f"\n{i}. [bold]{f.title}[/]")
        console.print(f"   likely: {f.cause}")
        console.print(f"   try:    {f.fix}")
        for e in f.evidence[:2]:
            console.print(f"   [dim]{e}[/]", markup=True, highlight=False)
    if args.post:
        import subprocess
        import sys

        lead = None
        try:
            import json
            from pathlib import Path
            leads = json.loads((Path.home() / ".quorus" / "room-leads.json").read_text())
            lead = leads.get(args.room)
        except (OSError, ValueError):
            pass
        text = retro.summary(findings, args.since, lead, args.top)
        subprocess.run([sys.executable, "-m", "quorus_cli.cli", "say", args.room, text],
                       check=False)
        console.print(f"\n[success]✓[/] posted to #{args.room}"
                      + (f" for @{lead}" if lead else ""))
