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


def _number(value: Any, lo: float, hi: float) -> float | None:
    """A finite number in [lo, hi], or None. Bools are not numbers here, and
    an int too big for a float (JSON allows ``1e400``-sized integers) is out
    of range rather than an OverflowError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int) and not lo <= value <= hi:
        return None  # compared as ints: float() of a huge int would overflow
    value = float(value)
    return value if math.isfinite(value) and lo <= value <= hi else None


def _epoch(value: Any) -> float | None:
    return _number(value, 0.0, EPOCH_MAX)


def clean_pause(raw: Any, now: float) -> dict[str, Any] | None:
    """``{"reason", "until"}`` if well-formed and still in the future."""
    if not isinstance(raw, dict) or set(raw) != {"reason", "until"}:
        return None
    until = _epoch(raw["until"])
    reason = raw["reason"]
    if not isinstance(reason, str) or reason not in PAUSE_REASONS or until is None \
            or until <= now:
        return None
    return {"reason": reason, "until": until}


def _clean_reading(raw: Any, now: float, pause: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not set(raw) <= READING_KEYS:
        return None
    window, kind, source = raw.get("window"), raw.get("kind"), raw.get("source")
    # Strings first: a list or object can't be looked up in a set (TypeError).
    if not (isinstance(window, str) and isinstance(kind, str) and isinstance(source, str)):
        return None
    if window not in WINDOWS or SOURCE_KIND.get(source) != kind:
        return None
    if (window == "limit") != (kind in ("observed", "guess")):
        return None  # usage windows come from reports; limit messages fill "limit"
    seen_at = _epoch(raw.get("seen_at"))
    if seen_at is None or seen_at > now + FUTURE_SKEW_S:
        return None
    used_pct = raw.get("used_pct")
    if kind == "reported":
        used_pct = _number(used_pct, 0.0, 100.0)
        if used_pct is None:
            return None
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
