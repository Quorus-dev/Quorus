"""Vendor limit readings, kept apart from Quorus's own pause (limit-status rev 3).

A *reading* is what the vendor told us: the exact reset time and where it came
from. The adapter's ``limited_until`` is the *pause*: when reflexd will next
wake the agent, with our padding and cap. The board shows the first and
schedules on the second, so the padding never reads as the vendor's reset.

``source`` is an id from a fixed list, never the vendor's text: harness stderr
can echo credentials or tool inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

LIMIT_DEFAULT_S = 30 * 60  # no reset time in the message: our guess
LIMIT_MAX_S = 24 * 3600  # the longest we pause before trying again
MAX_RESET_AHEAD_S = 8 * 24 * 3600  # beyond the 7-day window: not trusted

# source id -> the kind it implies (design rev 3, "Bounded fields")
SOURCE_KIND = {
    "msg.retry_in": "observed",
    "msg.try_again_at": "observed",
    "msg.no_time": "guess",
}

_RETRY_RE = re.compile(r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?",
                       re.IGNORECASE)
_AT_RE = re.compile(r"try again at\s+(\d{1,2}):(\d{2})\s*([AP]M)?", re.IGNORECASE)


@dataclass(frozen=True)
class LimitReading:
    window: str  # "limit" here; "5h" / "7d" come with reported usage (slice 3)
    kind: str  # "observed" | "guess"
    source: str  # a SOURCE_KIND key
    seen_at: float  # epoch seconds
    resets_at: float | None = None  # epoch seconds, exact (no padding, no cap)


def _at_target(match: re.Match[str], local_now: datetime) -> datetime | None:
    """The next local time matching "1:46 AM" / "13:46", or None if it isn't a
    real clock time ("1:99 AM", "25:00", "0:30 PM")."""
    hour, minute, ampm = int(match.group(1)), int(match.group(2)), (match.group(3) or "").upper()
    if minute > 59 or (hour > 23 if not ampm else not 1 <= hour <= 12):
        return None
    if ampm == "PM" and hour < 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > local_now else target + timedelta(days=1)


def read_limit_message(text: str, now: float,
                       local_now: datetime | None = None) -> tuple[LimitReading, float]:
    """A limit message -> (the vendor's reading, how long Quorus pauses).

    "retry in 21h39m46s" and Codex's "try again at 1:46 AM" give an exact
    reset; anything else, or a reset in the past or more than 8 days out,
    is a guess with no reset time; so is a clock time that isn't one
    ("1:99 AM", "25:00"). It never raises on what the vendor printed. The
    pause keeps 0.6.5's numbers: at least 60 s, at most 24 h, +60 s after a
    clock time, 30 min if unknown.
    """
    text = text or ""
    reset: float | None = None
    source, retry = "msg.no_time", float(LIMIT_DEFAULT_S)
    at = _AT_RE.search(text)
    m = _RETRY_RE.search(text)
    if at:
        local_now = local_now or datetime.now()
        target = _at_target(at, local_now)
        if target is not None:
            ahead = (target - local_now).total_seconds()
            reset, source = now + ahead, "msg.try_again_at"
            retry = max(60.0, min(LIMIT_MAX_S, ahead + 60))
    elif m and any(m.groups()):
        h, mi, se = (float(g) if g else 0.0 for g in m.groups())
        ahead = h * 3600 + mi * 60 + se
        reset, source = now + ahead, "msg.retry_in"
        retry = max(60.0, min(LIMIT_MAX_S, ahead))
    if reset is not None and not now < reset <= now + MAX_RESET_AHEAD_S:
        reset, source = None, "msg.no_time"
    return LimitReading("limit", SOURCE_KIND[source], source, now, reset), retry


def merge(current: LimitReading | None, new: LimitReading, now: float) -> LimitReading:
    """A guess never replaces an observed reset that is still ahead."""
    if (new.kind == "guess" and current is not None and current.resets_at is not None
            and current.resets_at > now):
        return current
    return new


def live(reading: LimitReading | None, now: float, pause_until: float,
         pause_reason: str) -> LimitReading | None:
    """The reading if it still holds. With a reset time it lasts until the
    reset, however old ``seen_at`` is; without one it lasts exactly as long
    as the quota pause, so an unknown reset can't keep "out of quota" up
    past our own retry."""
    if reading is None:
        return None
    if reading.resets_at is not None:
        return reading if reading.resets_at > now else None
    return reading if pause_reason == "quota" and pause_until > now else None
