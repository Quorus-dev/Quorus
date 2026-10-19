"""`quorus track-report`: what loop A's shadow logs say, per agent, last 24h.

Read-only over the daemon's own track logs in ~/.quorus/runtime
(quorus/runtime/track_record.py). It prints the numbers Codex and Claude agreed
to judge loop A by before anything routes on it: failed-turn rate by arm,
whether a low multiplier predicted a failed turn, the same for the per-path
track record, and how many turns had no matching bid. It writes nothing and
changes no bid.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from quorus.runtime import track_record

DEFAULT_RUNTIME_DIR = Path.home() / ".quorus" / "runtime"
# Below this many matched turns the rates are anecdotes, not evidence.
MIN_TURNS = 10
_TURNS_FILE = re.compile(r"^track-turns-(.+)\.json$")


def agents(runtime_dir: Path) -> list[str]:
    """Agents on this machine with a turns log."""
    try:
        names = [m.group(1) for p in runtime_dir.iterdir() if (m := _TURNS_FILE.match(p.name))]
    except OSError:
        return []
    return sorted(names)


def _rate(bucket: dict[str, int]) -> str:
    n, failed = bucket["turns"], bucket["failed"]
    return f"{n} turns, {round(100 * failed / n)}% failed" if n else "0 turns"


def render(name: str, rep: dict[str, Any]) -> list[str]:
    arms, pred, paths = rep["arms"], rep["predictive"], rep["path_predictive"]
    matched = sum(a["turns"] for a in arms.values())
    lines = [f"{name}: @open turns, last 24h ({matched} matched, {rep['unknown']} unmatched)"]
    lines += [f"  {arm:<8} {_rate(arms[arm])}" for arm in ("routed", "holdout", "shadow")]
    lines.append(f"  multiplier <1.0: {_rate(pred['low'])}; >=1.0: {_rate(pred['high'])}")
    lines.append(f"  path record <1.0: {_rate(paths['low'])}; >=1.0: {_rate(paths['high'])}; "
                 f"too thin: {paths['thin']}")
    if matched < MIN_TURNS:
        lines.append(f"  fewer than {MIN_TURNS} tasks so far: too few to say yet whether an "
                     "agent's track record predicts how its next task goes")
    return lines


def cmd_track_report(args: Any, console: Any) -> int:
    runtime_dir = Path(getattr(args, "runtime_dir", None) or DEFAULT_RUNTIME_DIR)
    names = [args.agent] if getattr(args, "agent", None) else agents(runtime_dir)
    reports = {n: track_record.report(runtime_dir, n) for n in names}
    if getattr(args, "json", False):
        print(json.dumps(reports, indent=2, sort_keys=True))
        return 0
    if not reports:
        console.print("Nothing to report yet. This shows how your agents' turns went when "
                      "you handed out work with `@open <task>` in a room (an available agent "
                      "that fits can take it). Hand out a few tasks that way, then check back.",
                      markup=False, highlight=False, soft_wrap=True)
        return 0
    for name, rep in reports.items():
        for line in render(name, rep):
            console.print(line, markup=False, highlight=False, soft_wrap=True)
    return 0
