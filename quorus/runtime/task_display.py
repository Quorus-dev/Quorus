"""Repo-local task cells. Descriptive history only; never assignment input."""
from __future__ import annotations

import math
import time
from collections import Counter, defaultdict

LABEL = "observed in this repo, not a ranking"
WINDOW_S = 30 * 86400
OUTCOMES = ("kept", "reverted", "failed", "abandoned", "escalated", "quota", "pending", "unknown")
RATE_OUTCOMES = ("kept", "reverted", "failed", "abandoned", "escalated")


def wilson(kept: int, settled: int) -> tuple[float, float]:
    """95% Wilson score interval (including the zero/all-success boundaries)."""
    if settled <= 0:
        return (0.0, 1.0)
    z = 1.959963984540054
    p = kept / settled
    den = 1 + z * z / settled
    mid = (p + z * z / (2 * settled)) / den
    half = z * math.sqrt(p * (1 - p) / settled + z * z / (4 * settled**2)) / den
    return max(0, mid - half), min(1, mid + half)


def cells(tasks: list[dict], now: float | None = None) -> list[dict]:
    """Keep model/version/kind cells separate and never count commits as tasks."""
    now = time.time() if now is None else now
    groups = defaultdict(list)
    seen = set()
    for task in tasks:
        identity = task.get("task")
        if identity in seen or not now - WINDOW_S <= task.get("claimed_at", 0) <= now:
            continue
        seen.add(identity)
        key = tuple(str(task.get(k) or "unknown") for k in ("agent", "model", "version", "kind"))
        groups[key].append(task)
    result = []
    for key, rows in sorted(groups.items()):
        counts = Counter(t.get("outcome", "unknown") for t in rows)
        settled = sum(counts[k] for k in OUTCOMES if k not in ("pending", "unknown"))
        # Quota describes availability. Escalation is still a non-kept original
        # assignment, as in the plan's 9 kept / 2 failed / 1 escalated example.
        rate_tasks = sum(counts[k] for k in RATE_OUTCOMES)
        kept = counts["kept"]
        done = [t for t in rows if t.get("outcome") not in ("pending", "unknown")]
        duration_unknown = sum(bool(t.get("duration_unknown")) or t.get("wall_s") is None
                               or t.get("attempt_count") == 0 for t in done)
        usage_measured = sum(t.get("tokens") is not None and t.get("attempt_count") != 0
                             for t in done)
        duration_measured = sum(t.get("wall_s") is not None and t.get("attempt_count") != 0
                                and not t.get("duration_unknown") for t in done)
        unknown = sum(bool(t.get("unknown_usage")) or t.get("tokens") is None
                      or t.get("attempt_count") == 0 for t in done)
        row = dict(zip(("agent", "model", "version", "kind"), key))
        row.update({k: counts[k] for k in OUTCOMES})
        row.update(tasks=len(rows), settled=settled, rate_tasks=rate_tasks,
                   rate=kept / rate_tasks if rate_tasks >= 5 else None,
                   interval=wilson(kept, rate_tasks) if rate_tasks >= 5 else None,
                   approved=sum(bool(t.get("approved")) for t in rows),
                   commits_published=sum(t.get("commits_published", 0) for t in rows),
                   commits_kept=sum(t.get("commits_kept", 0) for t in rows),
                   wall_s=sum(t.get("wall_s") or 0 for t in done),
                   tokens=sum(t.get("tokens") or 0 for t in done), unknown_usage=unknown,
                   duration_unknown=duration_unknown, usage_measured=usage_measured,
                   duration_measured=duration_measured)
        row["summary"] = summary(row)
        result.append(row)
    return result


def summary(cell: dict) -> str:
    n = cell["rate_tasks"]
    if cell["rate"] is None:
        text = f"{cell['kept']} kept · not enough history ({n})"
    else:
        lo, hi = cell["interval"]
        text = f"{cell['kept']} of {n} kept ({100 * lo:.0f}–{100 * hi:.0f}%)"
    text += ", " + ", ".join(f"{cell[k]} {k}" for k in OUTCOMES if k != "kept")
    text += " · rate excludes quota; includes escalated original assignments"
    text += (f" · {cell['commits_kept']}/{cell['commits_published']} commits kept"
             f" · {cell['approved']} approved")
    if cell["kept"] and cell["duration_measured"]:
        if cell["duration_unknown"]:
            text += f" · {cell['wall_s'] / 60:.1f} recorded attempt min (partial total)"
        else:
            text += f" · {cell['wall_s'] / cell['kept'] / 60:.1f} min per kept"
    if cell["kept"] and cell["usage_measured"]:
        if cell["unknown_usage"]:
            text += f" · {cell['tokens']:,.0f} recorded tokens (partial total)"
        else:
            text += f" · {cell['tokens'] / cell['kept']:,.0f} tokens per kept"
    text += f" · time unknown for {cell['duration_unknown']} of {cell['settled']} settled"
    text += f" · tokens unknown for {cell['unknown_usage']} of {cell['settled']} settled"
    return text


def line(tasks: list[dict], *, agent=None, model=None, version=None, kind=None,
         now: float | None = None) -> str:
    rows = cells(tasks, now)
    selected = [r for r in rows if all(v is None or r[k] == v for k, v in
                (("agent", agent), ("model", model), ("version", version), ("kind", kind))) ]
    parts = [f"{r['agent']} / {r['model']} / {r['version']} / {r['kind']}: {r['summary']}"
             for r in selected]
    return LABEL + " · " + ("; ".join(parts) or "not enough history (0) · 0 pending, 0 unknown")


def notice(tasks: list[dict], kind: str) -> str:
    return line(tasks, kind=kind)
