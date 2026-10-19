"""`quorus tune`: what your Quorus learned about you, and how to undo it.

Each adaptation (quorus/tuning.py) is local to this machine. `list` shows them
with their counts, `undo <id>` stops one until `redo <id>`, `off` returns every
agent to stock behaviour until `on`.
"""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from quorus import tuning


def cmd_tune(args: Any, console: Any) -> None:
    action = getattr(args, "action", None) or "list"
    if action in ("undo", "redo"):
        if not args.id:
            raise SystemExit(f"tune: which one? quorus tune {action} <id> (see quorus tune)")
        changed = (tuning.undo if action == "undo" else tuning.redo)(args.id)
        if changed is None:
            want = "active" if action == "undo" else "undone"
            raise SystemExit(f"tune: no {want} adaptation {args.id!r} (see quorus tune)")
        verb = "undone" if action == "undo" else "re-enabled"
        tail = f" (quorus tune redo {changed.id} brings it back)" if action == "undo" else ""
        console.print(f"{verb} {changed.id}: {escape(changed.change)}{tail}")
        return
    if action in ("off", "on"):
        tuning.set_enabled(action == "on")
        console.print("tuning on: your agents adapt to you again" if action == "on" else
                      "tuning off: every agent is back to stock behaviour now "
                      "(nothing is deleted; quorus tune on resumes)")
        return
    if getattr(args, "json", False):
        console.print(tuning.to_json(), markup=False, highlight=False)
        return
    rows = tuning.adaptations()
    if tuning.is_invalid():
        console.print(f"[warning]{tuning.path()} can't be read[/], so every agent is on "
                      "stock behaviour. quorus tune off (or on) starts a fresh file.")
    elif not tuning.enabled():
        console.print("[warning]tuning is off[/] (quorus tune on to resume)")
    if not rows:
        console.print("[dim]Nothing learned yet. When you hand out work with "
                      "`@open <task>` in a room, Quorus keeps track of how each agent's "
                      "turns go. If one keeps failing or its commits get reverted, it shows "
                      "up here, with one line in the room saying why and an undo. By default "
                      "these are notes only and no agent works differently because of them; "
                      "QUORUS_TRACK_BIDS=on lets them change which agent gets a task.[/]")
        return
    from rich.table import Table

    table = Table(title="What your Quorus learned", title_justify="left",
                  header_style="bold")
    for col in ("id", "change", "because", "state", "learned on"):
        table.add_column(col, no_wrap=col in ("id", "state"))
    for a in rows:
        style = "" if a.state == "active" else "dim"
        table.add_row(a.id, escape(a.change), escape(a.because), a.state,
                      escape(a.scope.replace("|", " · ")), style=style)
    console.print(table)
    console.print("[dim]quorus tune undo <id> stops one until redo; "
                  "quorus tune off stops all[/]")
