"""Read-only, owner-scoped task history for this repo."""
from __future__ import annotations

import json
from pathlib import Path

from rich.markup import escape

from quorus.config import load_config
from quorus.runtime import task_display, task_record
from quorus.services.approval_svc import agent_owner


def cmd_track(args, console) -> None:
    repo = Path(getattr(args, "repo", None) or ".").expanduser().resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"track: not a git repo: {repo}")
    cfg = load_config()
    instance = str(cfg.get("instance_name") or "")
    owner = getattr(args, "owner", None) or agent_owner(instance) or instance
    if not owner or owner == "default":
        raise SystemExit("track: specify --owner (whose task history to read)")
    relay = getattr(args, "relay", None) or cfg["relay_url"]
    tasks = task_record.report(repo, getattr(args, "main", None) or "main",
                               task_record.scope(relay, owner, repo))
    rows = task_display.cells(tasks)
    if getattr(args, "json", False):
        console.print(json.dumps({"label": task_display.LABEL, "days": 30, "cells": rows}),
                      markup=False, highlight=False)
        return
    from rich.table import Table
    table = Table(title=task_display.LABEL + " · 30 days")
    for col in ("agent", "model", "harness version", "kind", "task history"):
        table.add_column(col)
    for row in rows:
        table.add_row(*(escape(str(row[k]))
                        for k in ("agent", "model", "version", "kind", "summary")))
    console.print(table)
    if not rows:
        console.print("not enough history (0) · 0 pending, 0 unknown", markup=False)
