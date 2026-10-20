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
import secrets
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
# Loop A, shadow only (agreed with codex 2026-10-18): per-path evidence is logged
# beside each bid and measured, never applied. Paths an @open names, like
# quorus/routes/rooms.py; their directory is the scope the evidence is read for.
_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,6})(?![\w/])")
PATH_MIN_N = 3  # fewer commits than this under a path: too thin to count


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
    if failure_reason or reply.startswith("[reflexd]"):
        # Any daemon sentinel ("harness errored", "gemini not installed on
        # this host", ...) means the agent never answered.
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


def load_turns(runtime_dir: Path, participant: str, now: float | None = None, *,
               scope: str | None = None) -> list[dict]:
    """Turns in the window; with *scope*, only those recorded in that
    relay|owner|repo (rows from before scoping match only the empty scope)."""
    now = time.time() if now is None else now
    return [r for r in _load(runtime_dir, participant, "turns", now)
            if r.get("outcome") in OUTCOMES
            and (scope is None or r.get("scope", "") == scope)]


def load_bids(runtime_dir: Path, participant: str, now: float | None = None, *,
              scope: str | None = None) -> list[dict]:
    rows = _load(runtime_dir, participant, "bids", time.time() if now is None else now)
    return [r for r in rows if scope is None or r.get("scope", "") == scope]


def record_turn(runtime_dir: Path, participant: str, outcome: str, *,
                kind: str = "", message_id: str = "", scope: str = "", model: str = "",
                started: float | None = None, prediction_id: str = "",
                now: float | None = None) -> None:
    """Append one finished turn. *prediction_id* is the bid snapshot taken when
    the turn started; *started* is when it began (a later bid never predicts it)."""
    if outcome not in OUTCOMES or mode() == "off":
        return
    now = time.time() if now is None else now
    row: dict[str, Any] = {"ts": now, "outcome": outcome, "kind": kind, "scope": scope,
                           "model": model}
    if started is not None:
        row["started"] = started
    if message_id:
        row["message_id"] = message_id
    if prediction_id:
        row["prediction_id"] = prediction_id
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


# ── loop A: per-path evidence (shadow) ───────────────────────────────────


def path_scopes(text: str, limit: int = 5) -> list[str]:
    """Directory prefixes of the file paths *text* names, e.g. "quorus/routes/"."""
    seen: list[str] = []
    for path in _PATH.findall(text or ""):
        prefix = path.rsplit("/", 1)[0] + "/"
        if prefix not in seen and ".." not in prefix and not prefix.startswith("/"):
            seen.append(prefix)
        if len(seen) >= limit:
            break
    return seen


_files_cache: dict[str, tuple[float, list[tuple[str, str, frozenset[str]]], set[str]]] = {}


def _commits_with_files(repo: Path, now: float) -> tuple[list[tuple[str, str, frozenset[str]]],
                                                         set[str]]:
    """[(sha, agent, files)] for the repo's recent agent commits, and reverted shas."""
    hit = _files_cache.get(str(repo))
    if hit and now - hit[0] < LEDGER_TTL_S:
        return hit[1], hit[2]

    def git(*args: str) -> str:
        try:
            out = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo,
                                 capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.stdout if out.returncode == 0 else ""

    out = git("log", f"-n{LEDGER_MAX_COMMITS}", "--notes=quorus", "--name-only",
              "--format=%x1e%H%x1f%N%x1f", "HEAD")
    rows: list[tuple[str, str, frozenset[str]]] = []
    for record in out.split("\x1e"):
        parts = record.split("\x1f")
        if len(parts) < 3 or not parts[1].strip():
            continue
        try:
            meta = json.loads(parts[1].strip().splitlines()[0])
        except (ValueError, IndexError):
            continue
        agent = meta.get("agent") if isinstance(meta, dict) else None
        if agent:
            files = frozenset(f for f in parts[2].split("\n") if f.strip())
            rows.append((parts[0].strip(), str(agent), files))
    reverted = set(_REVERTS.findall(git("log", f"-n{LEDGER_MAX_COMMITS * 2}", "--format=%B",
                                        "HEAD")))
    _files_cache[str(repo)] = (now, rows, reverted)
    return rows, reverted


def path_counts(repo: Path | None, participant: str, scopes: list[str],
                now: float | None = None) -> tuple[int, int]:
    """(kept, reverted) commits by *participant* that touched any of *scopes*."""
    if repo is None or not scopes:
        return 0, 0
    rows, reverted = _commits_with_files(repo, time.time() if now is None else now)
    kept = rev = 0
    for sha, agent, files in rows:
        if agent != participant or not any(f.startswith(s) for f in files for s in scopes):
            continue
        if any(sha.startswith(r) for r in reverted):
            rev += 1
        else:
            kept += 1
    return kept, rev


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
              now: float | None = None, *, scope: str = "") -> Track:
    turns = load_turns(runtime_dir, participant, now, scope=scope)
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
    prediction_id: str = ""  # the logged bid row; the turn it predicts carries it


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
    parts = scope.split("|")
    new = tuning.adapt("bid", subject=participant, scope=scope,
                       owner=parts[1] if len(parts) >= 3 else "",
                       change=f"@open bids x{m:.2f} for @{participant}",
                       because=counts(track),
                       evidence={"direction": "down" if m <= ENTER_DOWN else "up",
                                 "multiplier": m, "availability": track.availability,
                                 "quality": track.quality})
    return new is not None, new


def decide_bid(bid: float, reason: str, *, message_id: str, runtime_dir: Path,
               participant: str, repo: Path | None, scope: str = "", harness: str = "",
               model: str = "", floor: float = 0.05, text: str = "") -> BidDecision:
    """The ``@open`` bid to submit, its reason, and a room line if one is due."""
    current = mode()
    if current == "off":
        return BidDecision(bid, reason)
    track = track_for(runtime_dir, participant, repo, scope=scope)
    scopes = path_scopes(text)
    p_kept, p_rev = path_counts(repo, participant, scopes)
    if not track.has_evidence and not (p_kept or p_rev):
        return BidDecision(bid, reason)  # no evidence yet: bid exactly as before
    now = time.time()
    applies, new = _sync(track, participant, scope, now)
    would = min(1.0, max(floor, bid * track.multiplier)) if applies else bid
    arm = "shadow" if current == "shadow" else ("holdout" if is_holdout(message_id)
                                                else "routed")
    prediction_id = secrets.token_hex(8)
    _append(runtime_dir, participant, "bids", {
        "ts": now, "prediction_id": prediction_id,
        "message_id": message_id, "arm": arm, "raw": bid, "would": would,
        "applies": applies, "availability": track.availability, "quality": track.quality,
        "multiplier": track.multiplier, "harness": harness, "model": model,
        "scope": scope,
        # loop A (shadow): logged to be measured, never part of `would`
        "paths": scopes, "path_kept": p_kept, "path_reverted": p_rev,
        "path_factor": round(_factor(min(p_kept, LEDGER_KEPT_CAP), p_rev), 2)}, now)
    announce = None
    if new is not None:
        if current == "shadow":
            announce = (f"(quorus) {new.id} (shadow, not applied): would set {new.change} "
                        f"· {new.because} · undo: quorus tune undo {new.id}")
        else:
            announce = (f"(quorus) adapted {new.id}: {new.change} · {new.because} "
                        f"· undo: quorus tune undo {new.id}")
    if arm == "routed" and applies:
        return BidDecision(would, f"{reason}+track {track.reason()}", announce, prediction_id)
    return BidDecision(bid, f"{reason}+track({arm}) {track.reason()}", announce, prediction_id)


def record_claim(runtime_dir: Path, participant: str, *, message_id: str, scope: str,
                 claim: dict[str, Any], now: float | None = None) -> None:
    """The auction's result as the relay announced it: who could have taken
    the ``@open``, who did, and with what bid. Recorded by every bidder, so
    an assignment is known before anyone's outcome is."""
    if mode() == "off" or not isinstance(claim, dict) or not claim.get("claimed"):
        return
    now = time.time() if now is None else now
    candidates = claim.get("candidates")
    _append(runtime_dir, participant, "claims", {
        "ts": now, "message_id": message_id, "scope": scope,
        "winner": str(claim.get("winner") or ""),
        "winning_bid": claim.get("bid") if isinstance(claim.get("bid"), (int, float)) else None,
        "candidates": [str(c) for c in candidates][:20] if isinstance(candidates, list) else [],
    }, now)


def load_claims(runtime_dir: Path, participant: str, now: float | None = None, *,
                scope: str | None = None) -> list[dict]:
    rows = _load(runtime_dir, participant, "claims", time.time() if now is None else now)
    return [r for r in rows if scope is None or r.get("scope", "") == scope]


def report(runtime_dir: Path, participant: str, now: float | None = None, *,
           scope: str | None = None) -> dict[str, Any]:
    """The L2 metrics for this agent's ``@open`` wins in the last 24h.

    * ``arms``: failed-turn rate by arm (routed vs holdout; shadow is stock).
    * ``predictive``: failed-turn rate when the would-be multiplier was below
      x1.0 vs at or above it. If low multipliers don't predict failures, the
      multiplier must not be switched on.
    """
    by_id = {b["prediction_id"]: b for b in load_bids(runtime_dir, participant, now,
                                                       scope=scope) if b.get("prediction_id")}

    def bid_before(turn: dict) -> dict | None:
        # Joined on the immutable bid snapshot the turn carried from its start,
        # never on message id + time: a retry's later bid can't relabel it, and a
        # model-B turn says nothing about a model-A prediction (codex, review of
        # f504e800). Anything unmatched is unknown, not a hit or a miss.
        bid = by_id.get(turn.get("prediction_id", ""))
        if (bid is None or bid.get("model", "") != turn.get("model", "")
                or bid["ts"] > turn.get("started", turn["ts"])):
            return None
        return bid

    arms = {a: {"turns": 0, "failed": 0} for a in ("routed", "holdout", "shadow")}
    predictive = {"low": {"turns": 0, "failed": 0}, "high": {"turns": 0, "failed": 0}}
    # Loop A (shadow): did this agent's track record on the paths an @open named
    # predict how its turn went? Only bids with PATH_MIN_N+ commits there count.
    path_predictive = {"low": {"turns": 0, "failed": 0}, "high": {"turns": 0, "failed": 0},
                       "thin": 0}
    unknown = 0
    for t in load_turns(runtime_dir, participant, now, scope=scope):
        if t.get("kind") != "open_todo":
            continue
        bid = bid_before(t)
        if not bid or bid.get("arm") not in arms:
            unknown += 1
            continue
        failed = t["outcome"] in FAILED
        for bucket in (arms[bid["arm"]],
                       predictive["low" if bid.get("multiplier", 1.0) < 1.0 else "high"]):
            bucket["turns"] += 1
            bucket["failed"] += failed
        if bid.get("paths"):
            if bid.get("path_kept", 0) + bid.get("path_reverted", 0) < PATH_MIN_N:
                path_predictive["thin"] += 1
            else:
                bucket = path_predictive["low" if bid.get("path_factor", 1.0) < 1.0 else "high"]
                bucket["turns"] += 1
                bucket["failed"] += failed
    # Descriptive counts only; no held-out accuracy yet (loop A plan, codex).
    return {"arms": arms, "predictive": predictive, "path_predictive": path_predictive,
            "unknown": unknown}
