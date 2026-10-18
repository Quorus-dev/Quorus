"""Track-record bids (L2): an agent's own outcomes inform its ``@open`` bid.

Two kinds of evidence, both local to this machine and kept apart, because an
agent that is out of quota is not one that writes bad code:

* availability: how this agent's harness turns ended over the last 24h
  (clean, errored, timed out, vendor failure such as quota);
* quality: its published commits on the room's bound repo (provenance git
  notes) and how many were later reverted. No label means unknown, not good.

Each is a Laplace-smoothed rate mapped to a factor that is exactly x1.0 with no
evidence; their product, clipped to x0.5-x1.3, is the multiplier.

Modes (``QUORUS_TRACK_BIDS``), stock behaviour first:

* ``shadow`` (default): bids stay raw. The would-be multiplier is logged and
  shown in the bid reason, so we can test whether it predicts failed turns
  before it is allowed to change who wins.
* ``on``: the multiplier is applied, except on a holdout of about 10% of
  ``@open``s picked by a hash of the message id (every bidder agrees).
* ``off``: nothing is computed or logged.

Every bid is logged before its outcome exists (arm, raw and would-be bid, both
factors, harness), and every turn from an ``@open`` names its message, so
:func:`report` can join them.
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

from quorus import tuning
from quorus.private_files import write_private_json

WINDOW_S = 24 * 3600.0
MIN_MULT, MAX_MULT = 0.5, 1.3
HOLDOUT_PERCENT = 10
LEDGER_TTL_S = 600.0
LEDGER_MAX_COMMITS = 300
# Published-and-kept is weak evidence (unreviewed work is "kept" too) and grows
# with volume, so it counts for at most this many good outcomes.
LEDGER_KEPT_CAP = 10
_MAX_ROWS = 500
FAILED = ("errored", "timed_out", "vendor_failure")
OUTCOMES = ("clean", *FAILED)
MODES = ("off", "shadow", "on")
# Hysteresis: an adaptation starts past the outer line and ends only once the
# multiplier is back inside the inner one, so it can't flap; a new one for the
# same agent waits out the cooldown.
ENTER_DOWN, EXIT_DOWN = 0.8, 0.9
ENTER_UP, EXIT_UP = 1.2, 1.1
COOLDOWN_S = 3600.0
_REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


def mode() -> str:
    """off | shadow | on. ``quorus tune off`` beats the environment."""
    if not tuning.enabled():
        return "off"
    raw = os.environ.get("QUORUS_TRACK_BIDS", "shadow").strip().lower()
    if raw in ("0", "false", "no"):
        return "off"
    if raw in ("1", "true", "yes"):
        return "on"
    return raw if raw in MODES else "shadow"


def is_holdout(message_id: str) -> bool:
    """Same answer on every machine for the same ``@open``."""
    digest = hashlib.sha256((message_id or "").encode("utf-8")).digest()
    return digest[0] % 100 < HOLDOUT_PERCENT


def classify_turn(reply: str, *, vendor_failure: bool, failure_reason: str,
                  slow_start: bool = False) -> str:
    """Map a finished harness turn to one of :data:`OUTCOMES`."""
    if vendor_failure:
        return "vendor_failure"
    if slow_start or failure_reason == "timed out" or reply == "[reflexd] harness timed out":
        return "timed_out"
    if failure_reason or reply == "[reflexd] harness errored":
        return "errored"
    return "clean"


# ── per-agent logs (this machine, last 24h) ──────────────────────────────


def _path(runtime_dir: Path, participant: str, log: str) -> Path:
    return runtime_dir / f"track-{log}-{participant}.json"


def _load(runtime_dir: Path, participant: str, log: str, now: float) -> list[dict]:
    try:
        data = json.loads(_path(runtime_dir, participant, log).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data if isinstance(data, list) else []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("ts"), (int, float))
            and now - r["ts"] < WINDOW_S]


def _append(runtime_dir: Path, participant: str, log: str, row: dict, now: float) -> None:
    # One writer per file (each agent's own daemon); writes are atomic.
    rows = _load(runtime_dir, participant, log, now) + [row]
    try:
        write_private_json(_path(runtime_dir, participant, log), rows[-_MAX_ROWS:])
    except OSError:
        pass  # best effort: bidding must never break a wake


def load_turns(runtime_dir: Path, participant: str, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    return [r for r in _load(runtime_dir, participant, "turns", now)
            if r.get("outcome") in OUTCOMES]


def load_bids(runtime_dir: Path, participant: str, now: float | None = None) -> list[dict]:
    return _load(runtime_dir, participant, "bids", time.time() if now is None else now)


def record_turn(runtime_dir: Path, participant: str, outcome: str, *,
                kind: str = "", message_id: str = "", now: float | None = None) -> None:
    """Append one finished turn; ``message_id`` links an ``@open`` win to its bid."""
    if outcome not in OUTCOMES or mode() == "off":
        return
    now = time.time() if now is None else now
    row: dict[str, Any] = {"ts": now, "outcome": outcome, "kind": kind}
    if message_id:
        row["message_id"] = message_id
    _append(runtime_dir, participant, "turns", row, now)


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


def _factor(good: int, bad: int) -> float:
    s = (good + 1) / (good + bad + 2)  # Laplace: no evidence → 0.5 → x1.0
    if s >= 0.5:
        return 1.0 + (s - 0.5) / 0.5 * (MAX_MULT - 1.0)
    return MIN_MULT + s / 0.5 * (1.0 - MIN_MULT)


@dataclass(frozen=True)
class Track:
    clean: int = 0
    errored: int = 0
    timeouts: int = 0
    vendor: int = 0
    kept: int = 0
    reverted: int = 0

    @property
    def failed(self) -> int:
        return self.errored + self.timeouts + self.vendor

    @property
    def has_evidence(self) -> bool:
        return bool(self.clean or self.failed or self.kept or self.reverted)

    @property
    def availability(self) -> float:
        return round(_factor(self.clean, self.failed), 2)

    @property
    def quality(self) -> float:
        return round(_factor(min(self.kept, LEDGER_KEPT_CAP), self.reverted), 2)

    @property
    def multiplier(self) -> float:
        return round(min(MAX_MULT, max(MIN_MULT, self.availability * self.quality)), 2)

    def reason(self) -> str:
        why = [f"{k} {n}" for k, n in (("timeout", self.timeouts), ("vendor", self.vendor),
                                        ("error", self.errored)) if n]
        avail = f"avail {self.clean}✓ {self.failed}✗/24h" + (f" ({', '.join(why)})"
                                                            if why else "")
        parts = [avail]
        if self.kept or self.reverted:
            parts.append(f"quality {self.kept} kept {self.reverted} reverted")
        return f"x{self.multiplier:.2f}: " + "; ".join(parts)


def track_for(runtime_dir: Path, participant: str, repo: Path | None,
              now: float | None = None) -> Track:
    turns = load_turns(runtime_dir, participant, now)
    published, reverted = ledger_counts(repo, participant, now)

    def n(outcome: str) -> int:
        return sum(1 for t in turns if t["outcome"] == outcome)

    return Track(clean=n("clean"), errored=n("errored"), timeouts=n("timed_out"),
                 vendor=n("vendor_failure"), kept=published - reverted, reverted=reverted)


def counts(track: Track) -> str:
    """What moved the bid, as counts only. Availability is never called quality."""
    def n(count: int, word: str) -> str:
        return f"{count} {word}{'' if count == 1 else 's'}"

    avail = [n(c, w) for c, w in ((track.vendor, "vendor fail"), (track.timeouts, "timeout"),
                                  (track.errored, "error")) if c]
    avail.append(f"{track.clean} clean")
    out = f"x{track.multiplier:.2f} · {', '.join(avail)}/24h"
    total = track.kept + track.reverted
    if total:
        out += f" · {track.reverted} of {total} commits reverted"
    return out


@dataclass(frozen=True)
class BidDecision:
    bid: float
    reason: str
    announce: str | None = None


def _sync(track: Track, participant: str, scope: str, now: float) -> tuple[bool, Any]:
    """(does an adaptation apply now, the new one to announce or None)."""
    m = track.multiplier
    last = tuning.latest("bid", participant, scope)
    if last is not None and last.state == "undone":
        return False, None  # the user said no; only `tune redo` brings it back
    if last is not None and last.state == "active":
        down = last.evidence.get("direction") == "down"
        if (down and m >= EXIT_DOWN) or (not down and m <= EXIT_UP):
            tuning.retire(last.id)
            return False, None
        return True, None
    if ENTER_DOWN < m < ENTER_UP:
        return False, None
    if last is not None and now - last.created_ts < COOLDOWN_S:
        return False, None
    new = tuning.adapt("bid", subject=participant, scope=scope,
                       change=f"@open bids x{m:.2f} for @{participant}",
                       because=counts(track),
                       evidence={"direction": "down" if m <= ENTER_DOWN else "up",
                                 "multiplier": m, "availability": track.availability,
                                 "quality": track.quality})
    return new is not None, new


def decide_bid(bid: float, reason: str, *, message_id: str, runtime_dir: Path,
               participant: str, repo: Path | None, scope: str = "", harness: str = "",
               floor: float = 0.05) -> BidDecision:
    """The ``@open`` bid to submit, its reason, and a room line if one is due."""
    current = mode()
    if current == "off":
        return BidDecision(bid, reason)
    track = track_for(runtime_dir, participant, repo)
    if not track.has_evidence:
        return BidDecision(bid, reason)  # no evidence yet: bid exactly as before
    now = time.time()
    applies, new = _sync(track, participant, scope, now)
    would = min(1.0, max(floor, bid * track.multiplier)) if applies else bid
    arm = "shadow" if current == "shadow" else ("holdout" if is_holdout(message_id)
                                                else "routed")
    _append(runtime_dir, participant, "bids", {
        "ts": now, "message_id": message_id, "arm": arm, "raw": bid, "would": would,
        "applies": applies, "availability": track.availability, "quality": track.quality,
        "multiplier": track.multiplier, "harness": harness, "scope": scope}, now)
    announce = None
    if new is not None:
        if current == "shadow":
            announce = (f"(quorus) {new.id} (shadow, not applied): would set {new.change} "
                        f"· {new.because} · undo: quorus tune undo {new.id}")
        else:
            announce = (f"(quorus) adapted {new.id}: {new.change} · {new.because} "
                        f"· undo: quorus tune undo {new.id}")
    if arm == "routed" and applies:
        return BidDecision(would, f"{reason}+track {track.reason()}", announce)
    return BidDecision(bid, f"{reason}+track({arm}) {track.reason()}", announce)


def report(runtime_dir: Path, participant: str, now: float | None = None) -> dict[str, Any]:
    """The L2 metrics for this agent's ``@open`` wins in the last 24h.

    * ``arms``: failed-turn rate by arm (routed vs holdout; shadow is stock).
    * ``predictive``: failed-turn rate when the would-be multiplier was below
      x1.0 vs at or above it. If low multipliers don't predict failures, the
      multiplier must not be switched on.
    """
    by_message: dict[Any, list[dict]] = {}
    for b in sorted(load_bids(runtime_dir, participant, now), key=lambda b: b["ts"]):
        by_message.setdefault(b.get("message_id"), []).append(b)

    def bid_before(turn: dict) -> dict | None:
        # The prediction made before this outcome existed; a later bid on the
        # same message (a retry) must never relabel it.
        earlier = [b for b in by_message.get(turn.get("message_id"), [])
                   if b["ts"] <= turn["ts"]]
        return earlier[-1] if earlier else None

    arms = {a: {"turns": 0, "failed": 0} for a in ("routed", "holdout", "shadow")}
    predictive = {"low": {"turns": 0, "failed": 0}, "high": {"turns": 0, "failed": 0}}
    for t in load_turns(runtime_dir, participant, now):
        bid = bid_before(t) if t.get("kind") == "open_todo" else None
        if not bid or bid.get("arm") not in arms:
            continue
        failed = t["outcome"] in FAILED
        for bucket in (arms[bid["arm"]],
                       predictive["low" if bid.get("multiplier", 1.0) < 1.0 else "high"]):
            bucket["turns"] += 1
            bucket["failed"] += failed
    return {"arms": arms, "predictive": predictive}
