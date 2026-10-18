"""Track-record bids (L2): an agent's own outcomes scale its ``@open`` bid.

Two signals, both local to this machine and this user:

* turns: how this agent's harness turns ended over the last 24h (clean,
  errored, timed out, vendor failure), kept in the daemon's runtime dir;
* ledger: its published commits on the room's bound repo (provenance git
  notes) and how many of those were later reverted.

They combine into one Laplace-smoothed success rate ``s``, mapped to a
multiplier between x0.5 and x1.3 that is exactly x1.0 with no evidence, so a
new agent bids as it always did. The evidence goes in the bid reason, so the
room can see why a bid moved.

Holdout: about 10% of ``@open`` messages use raw bids. The pick is a hash of
the message id, so every agent in an auction makes the same choice. Each turn
records whether it came from a holdout ``@open``, which gives the metric:
failed turns per ``@open`` win, routed vs holdout.

``QUORUS_TRACK_BIDS=0`` turns it off.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quorus.private_files import write_private_json

WINDOW_S = 24 * 3600.0
MIN_MULT, MAX_MULT = 0.5, 1.3
HOLDOUT_PERCENT = 10
LEDGER_TTL_S = 600.0
LEDGER_MAX_COMMITS = 300
# Published-and-kept is weak evidence (unreviewed work is "kept" too) and grows
# with volume, so it counts for at most this many good outcomes.
LEDGER_KEPT_CAP = 10
_MAX_TURNS = 500
FAILED = ("errored", "timed_out", "vendor_failure")
OUTCOMES = ("clean", *FAILED)
_REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


def enabled() -> bool:
    return os.environ.get("QUORUS_TRACK_BIDS", "1").strip().lower() not in ("0", "off", "false")


def is_holdout(message_id: str) -> bool:
    """Same answer on every machine for the same ``@open``."""
    digest = hashlib.sha256((message_id or "").encode("utf-8")).digest()
    return digest[0] % 100 < HOLDOUT_PERCENT


def classify_turn(reply: str, *, vendor_failure: bool, failure_reason: str) -> str:
    """Map a finished harness turn to one of :data:`OUTCOMES`."""
    if vendor_failure:
        return "vendor_failure"
    if failure_reason == "timed out" or reply == "[reflexd] harness timed out":
        return "timed_out"
    if failure_reason or reply == "[reflexd] harness errored":
        return "errored"
    return "clean"


# ── turns (this machine, last 24h) ───────────────────────────────────────


def _turns_path(runtime_dir: Path, participant: str) -> Path:
    return runtime_dir / f"track-{participant}.json"


def load_turns(runtime_dir: Path, participant: str, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    try:
        data = json.loads(_turns_path(runtime_dir, participant).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data if isinstance(data, list) else []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("ts"), (int, float))
            and now - r["ts"] < WINDOW_S and r.get("outcome") in OUTCOMES]


def record_turn(runtime_dir: Path, participant: str, outcome: str, *,
                kind: str = "", holdout: bool | None = None,
                now: float | None = None) -> None:
    """Append one finished turn. Best effort: bidding must never break a wake."""
    if outcome not in OUTCOMES:
        return
    now = time.time() if now is None else now
    rows = load_turns(runtime_dir, participant, now)
    row: dict[str, Any] = {"ts": now, "outcome": outcome, "kind": kind}
    if holdout is not None:
        row["holdout"] = holdout
    rows.append(row)
    try:
        write_private_json(_turns_path(runtime_dir, participant), rows[-_MAX_TURNS:])
    except OSError:
        pass


# ── ledger (the bound repo's provenance notes) ───────────────────────────

_ledger_cache: dict[tuple[str, str], tuple[float, tuple[int, int]]] = {}


def ledger_counts(repo: Path | None, participant: str,
                  now: float | None = None) -> tuple[int, int]:
    """(published, reverted) commits by *participant* in *repo*'s recent history."""
    if repo is None:
        return 0, 0
    now = time.time() if now is None else now
    key = (str(repo), participant)
    hit = _ledger_cache.get(key)
    if hit and now - hit[0] < LEDGER_TTL_S:
        return hit[1]
    counts = _read_ledger(repo, participant)
    _ledger_cache[key] = (now, counts)
    return counts


def _read_ledger(repo: Path, participant: str) -> tuple[int, int]:
    # One git call for notes, one for revert trailers; hooks off, like the ledger.
    def git(*args: str) -> str:
        try:
            out = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo,
                                 capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.stdout if out.returncode == 0 else ""

    log = git("log", f"-n{LEDGER_MAX_COMMITS}", "--notes=quorus",
              "--format=%H%x1f%N%x1e", "HEAD")
    mine: set[str] = set()
    for record in log.split("\x1e"):
        sha, _, note = record.strip().partition("\x1f")
        if not note.strip():
            continue
        try:
            meta = json.loads(note.strip().splitlines()[0])
        except (ValueError, IndexError):
            continue
        if isinstance(meta, dict) and meta.get("agent") == participant:
            mine.add(sha)
    if not mine:
        return 0, 0
    reverted = set(_REVERTS.findall(git("log", f"-n{LEDGER_MAX_COMMITS * 2}",
                                        "--format=%B", "HEAD")))
    hit = {s for s in mine if any(s.startswith(r) for r in reverted)}
    return len(mine), len(hit)


# ── the multiplier ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Track:
    clean: int = 0
    failed: int = 0
    timeouts: int = 0
    kept: int = 0
    reverted: int = 0

    @property
    def multiplier(self) -> float:
        good = self.clean + min(self.kept, LEDGER_KEPT_CAP)
        bad = self.failed + self.reverted
        s = (good + 1) / (good + bad + 2)  # Laplace: no evidence → 0.5 → x1.0
        if s >= 0.5:
            m = 1.0 + (s - 0.5) / 0.5 * (MAX_MULT - 1.0)
        else:
            m = MIN_MULT + s / 0.5 * (1.0 - MIN_MULT)
        return round(min(MAX_MULT, max(MIN_MULT, m)), 2)

    def reason(self) -> str:
        parts = [f"{self.clean}✓ {self.failed}✗/24h"]
        if self.timeouts:
            parts.append(f"{self.timeouts} timeout{'s' if self.timeouts != 1 else ''}")
        if self.kept or self.reverted:
            parts.append(f"ledger {self.kept} kept {self.reverted} reverted")
        return f"track x{self.multiplier:.2f}: " + ", ".join(parts)


def track_for(runtime_dir: Path, participant: str, repo: Path | None,
              now: float | None = None) -> Track:
    turns = load_turns(runtime_dir, participant, now)
    published, reverted = ledger_counts(repo, participant, now)
    return Track(
        clean=sum(1 for t in turns if t["outcome"] == "clean"),
        failed=sum(1 for t in turns if t["outcome"] in FAILED),
        timeouts=sum(1 for t in turns if t["outcome"] == "timed_out"),
        kept=published - reverted, reverted=reverted,
    )


def adjust_bid(bid: float, reason: str, *, message_id: str, runtime_dir: Path,
               participant: str, repo: Path | None,
               floor: float = 0.05) -> tuple[float, str]:
    """Scale an ``@open`` bid by this agent's track record (or leave it raw)."""
    if not enabled():
        return bid, reason
    track = track_for(runtime_dir, participant, repo)
    if not (track.clean or track.failed or track.kept or track.reverted):
        return bid, reason  # no evidence yet: bid exactly as before
    if is_holdout(message_id):
        return bid, f"{reason}+holdout"
    scaled = min(1.0, max(floor, bid * track.multiplier))
    return scaled, f"{reason}+{track.reason()}"


def report(runtime_dir: Path, participant: str, now: float | None = None) -> dict[str, Any]:
    """The L2 metric for this agent: failed-turn rate after @open wins, by arm."""
    arms: dict[str, dict[str, int]] = {"routed": {"turns": 0, "failed": 0},
                                       "holdout": {"turns": 0, "failed": 0}}
    for t in load_turns(runtime_dir, participant, now):
        if t.get("kind") != "open_todo" or "holdout" not in t:
            continue
        arm = arms["holdout" if t["holdout"] else "routed"]
        arm["turns"] += 1
        arm["failed"] += t["outcome"] in FAILED
    return arms
