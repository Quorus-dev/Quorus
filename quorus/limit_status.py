"""Limit status on presence: validate the daemon's ``pause`` and ``readings``.

Design: docs/internal/bets/limit-status.md rev 3 ("Bounded fields"). Both
fields are optional on the heartbeat. A bad reading is dropped and the rest of
the heartbeat is kept: one bad parse must not take an agent off the board. The
same check runs again when presence is read, so a reading that expired since
it was stored is not shown.

``source`` is an id from a fixed list. Vendor text never reaches the relay.
"""

from __future__ import annotations

import math
from typing import Any

EPOCH_MAX = 4102444800.0  # same bound as ``waiting_until``
FUTURE_SKEW_S = 300.0  # a daemon clock this far ahead is still accepted
USAGE_MAX_AGE_S = 3600.0  # 5h / 7d usage older than this is dropped
RESET_MAX_AHEAD_S = 8 * 24 * 3600.0  # beyond the 7-day window: not trusted
MAX_READINGS = 3  # one per window

PAUSE_REASONS = frozenset({"quota", "slow_start", "no_route"})
WINDOWS = frozenset({"5h", "7d", "limit"})
# source id -> the kind it implies
SOURCE_KIND = {
    "claude.statusline": "reported",
    "claude.stream": "reported",
    "codex.token_count": "reported",
    "msg.retry_in": "observed",
    "msg.try_again_at": "observed",
    "msg.no_time": "guess",
}
READING_KEYS = frozenset({"window", "kind", "source", "used_pct", "resets_at", "seen_at"})


def _epoch(value: Any) -> float | None:
    """A finite epoch in range, or None. Bools are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0.0 <= value <= EPOCH_MAX else None


def clean_pause(raw: Any, now: float) -> dict[str, Any] | None:
    """``{"reason", "until"}`` if well-formed and still in the future."""
    if not isinstance(raw, dict) or set(raw) != {"reason", "until"}:
        return None
    until = _epoch(raw["until"])
    if raw["reason"] not in PAUSE_REASONS or until is None or until <= now:
        return None
    return {"reason": raw["reason"], "until": until}


def _clean_reading(raw: Any, now: float, pause: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not set(raw) <= READING_KEYS:
        return None
    window, kind, source = raw.get("window"), raw.get("kind"), raw.get("source")
    if window not in WINDOWS or not isinstance(source, str) or SOURCE_KIND.get(source) != kind:
        return None
    if (window == "limit") != (kind in ("observed", "guess")):
        return None  # usage windows come from reports; limit messages fill "limit"
    seen_at = _epoch(raw.get("seen_at"))
    if seen_at is None or seen_at > now + FUTURE_SKEW_S:
        return None
    used_pct = raw.get("used_pct")
    if kind == "reported":
        if (isinstance(used_pct, bool) or not isinstance(used_pct, (int, float))
                or not math.isfinite(used_pct) or not 0.0 <= used_pct <= 100.0):
            return None
        used_pct = float(used_pct)
    elif used_pct is not None:
        return None
    resets_at = raw.get("resets_at")
    if resets_at is not None:
        resets_at = _epoch(resets_at)
        if (resets_at is None or kind == "guess" or resets_at <= seen_at
                or resets_at > seen_at + RESET_MAX_AHEAD_S or resets_at <= now):
            return None
    if window in ("5h", "7d"):
        if seen_at < now - USAGE_MAX_AGE_S:
            return None
    elif resets_at is not None:
        if seen_at < now - RESET_MAX_AHEAD_S:
            return None  # a live reset holds until it passes, however old seen_at is
    elif pause is None or pause["reason"] != "quota":
        return None  # no reset time: it lives exactly as long as a quota pause
    return {"window": window, "kind": kind, "source": source, "used_pct": used_pct,
            "resets_at": resets_at, "seen_at": seen_at}


def clean(pause: Any, readings: Any, now: float) -> dict[str, Any]:
    """The valid part of a heartbeat's limit fields; ``{}`` if there is none."""
    out: dict[str, Any] = {}
    p = clean_pause(pause, now)
    if p is not None:
        out["pause"] = p
    if isinstance(readings, list) and len(readings) <= MAX_READINGS:
        kept = [r for r in (_clean_reading(x, now, p) for x in readings) if r is not None]
        windows = [r["window"] for r in kept]
        kept = [r for r in kept if windows.count(r["window"]) == 1]  # one per window
        if kept:
            out["readings"] = kept
    return out
