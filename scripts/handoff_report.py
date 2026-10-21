"""Never Stuck dogfood report: the plan §4 table, read-only (slice 2c-3b).

    python scripts/handoff_report.py --state ~/.qd/claude --state ~/.qd/codex

Each ``--state`` is one daemon's config dir (its QUORUS_CONFIG_DIR): its
``runtime/handoffs-*.json`` journals, their ``-events.jsonl`` and its
bindings file. One row per sender attempt, joined to the receiver's journal
entry by capsule fingerprint. It reads files and runs read-only git; it writes
nothing.

Rows from a room whose name ends in ``-rehearsal`` are reported in their own table
and never enter the totals (dogfood plan §6: an injected limit is not a real one).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quorus.runtime.bindings import FILENAME as BINDINGS_FILE

REHEARSAL_SUFFIX = "-rehearsal"


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def load(state_dirs: list[Path]) -> tuple[list[dict], dict[str, list[dict]], dict]:
    """(sender events, every receiver entry by fingerprint, bindings by state dir).

    A capsule declined by one receiver and taken by the next has an entry on each
    side: all of them are kept, never one overwriting another (Codex on 93a0cb85),
    and the result below doesn't depend on the order of --state."""
    events, entries, bindings = [], {}, {}
    for state in state_dirs:
        bindings[state] = _json(state / BINDINGS_FILE) or {}
        for journal in sorted((state / "runtime").glob("handoffs-*.json")):
            for entry in (_json(journal) or {}).values():
                if isinstance(entry, dict) and entry.get("state") not in (None, "sent"):
                    entries.setdefault(entry.get("fingerprint", ""), []).append(
                        {**entry, "_state_dir": state})
        for log in sorted((state / "runtime").glob("handoffs-*-events.jsonl")):
            for line in log.read_text().splitlines():
                try:
                    events.append({**json.loads(line), "_state_dir": state})
                except ValueError:
                    continue
    return events, entries, bindings


def _seconds(start: str | None, end: str | None) -> float | None:
    try:
        return (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))
                ).total_seconds()
    except (TypeError, ValueError):
        return None


# Best outcome first: a handoff that a later receiver reproduced counts as reproduced.
RANK = ("reproduced", "weak", "already_passing", "stale", "unverifiable", "declined")


def _rank(entry: dict) -> tuple:
    outcome = entry.get("outcome") or entry.get("state") or ""
    return (RANK.index(outcome) if outcome in RANK else len(RANK), outcome,
            str(entry.get("check_finished") or ""), str(entry["_state_dir"]))


def _check_differs(ran: dict | None, bindings: dict, room: str) -> bool:
    """Both the argv and the config_id that ran must equal the room's binding
    (Codex on 93a0cb85: a matching id beside different argv went unflagged)."""
    from quorus.runtime.check_config import CheckConfig

    try:
        want = CheckConfig.from_record(bindings[room]["check"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return True
    ran = ran or {}
    return ran.get("config_id") != want.config_id or list(ran.get("argv") or []) != list(
        want.argv)


def _patch_ids(repo: Path, rng: str) -> set[str]:
    try:
        commits = subprocess.run(["git", "rev-list", rng], cwd=repo, capture_output=True,
                                 text=True, timeout=30, check=True).stdout.split()
        out = set()
        for commit in commits:
            diff = subprocess.run(["git", "show", commit], cwd=repo, capture_output=True,
                                  timeout=30, check=True).stdout
            pid = subprocess.run(["git", "patch-id", "--stable"], input=diff,
                                 capture_output=True, timeout=30, check=True).stdout.split()
            if pid:
                out.add(pid[0].decode())
        return out
    except (OSError, subprocess.SubprocessError):
        return set()


def repeats(entry: dict) -> int | None:
    """Commits after the handoff that repeat one from before it (the §4 bar), or None
    when the continuation hasn't finished."""
    done, snap = entry.get("completed_head"), entry.get("snapshot") or {}
    repo = Path(str(entry.get("scope", "")).partition(":")[2])
    base, head = (snap.get("repo") or {}).get("base"), (snap.get("repo") or {}).get("head")
    if not (done and base and head and repo.is_dir()):
        return None
    return len(_patch_ids(repo, f"{base}..{head}") & _patch_ids(repo, f"{head}..{done}"))


def rows(events: list[dict], entries: dict[str, dict], bindings: dict) -> list[dict]:
    by_attempt: dict[str, dict] = {}
    for e in events:
        row = by_attempt.setdefault(e.get("attempt_id", "?"), {
            "attempt": str(e.get("attempt_id", "?"))[:8], "room": e.get("room", ""),
            "result": "attempted", "flags": []})
        kind = e.get("event")
        if kind == "attempt_failed":
            row["result"] = f"failed ({e.get('cause', '?')})"
        elif kind == "capsule_built":
            row.update(fingerprint=e.get("fingerprint"), capsule_bytes=e.get("capsule_bytes"),
                       seen_at=e.get("seen_at"), seen_kind=e.get("limit_kind"))
            if _check_differs(e.get("check"), bindings.get(e["_state_dir"], {}), row["room"]):
                row["flags"].append("sender check differs from binding")
        elif kind == "sent":
            row.update(result="sent", envelope_bytes=e.get("envelope_bytes"))
    for row in by_attempt.values():
        found = sorted(entries.get(row.get("fingerprint") or "", []), key=_rank)
        if not found:
            continue
        entry = found[0]
        row["result"] = entry.get("outcome") or entry.get("state")
        row["receivers"] = ", ".join(e.get("outcome") or e.get("state") or "?" for e in found)
        row["retries"] = sum(len(e.get("retry_wakes") or []) for e in found)
        row["resume_s"] = _seconds(row.get("seen_at"), entry.get("check_finished"))
        for e in found:
            if e.get("check_ran") and _check_differs(
                    e["check_ran"], bindings.get(e["_state_dir"], {}), row["room"]):
                row["flags"].append("receiver check differs from binding")
                break
        row["repeats"] = repeats(entry)
        if row["repeats"]:
            row["flags"].append("repeated commits")
    return sorted(by_attempt.values(), key=lambda r: (r["room"], r["attempt"]))


def render(table: list[dict], title: str) -> str:
    lines = [f"## {title}", "",
             "| attempt | room | result | receivers | retries | resume s | capsule B "
             "| envelope B | seen_at kind | repeats | flags |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in table:
        resume = r.get("resume_s")
        lines.append(" | ".join([
            f"| {r['attempt']}", r["room"], str(r["result"]), r.get("receivers", "-"),
            str(r.get("retries", "-")), "-" if resume is None else f"{resume:.1f}",
            str(r.get("capsule_bytes", "-")), str(r.get("envelope_bytes", "-")),
            str(r.get("seen_kind", "-")), str(r.get("repeats", "-")),
            "; ".join(r["flags"]) or "-"]) + " |")
    counts = Counter(str(r["result"]).split(" ")[0] for r in table)
    lines += ["", "totals: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                                or "none")]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state", action="append", type=Path, required=True,
                   help="a daemon's config dir (repeat for sender and receiver)")
    args = p.parse_args(argv)
    table = rows(*load([s.expanduser() for s in args.state]))
    real = [r for r in table if not r["room"].endswith(REHEARSAL_SUFFIX)]
    rehearsal = [r for r in table if r["room"].endswith(REHEARSAL_SUFFIX)]
    print(render(real, "handoffs"))
    if rehearsal:
        print("\n" + render(rehearsal, "rehearsal (injected limits; not in the totals)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
