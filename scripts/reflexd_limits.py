"""Vendor limit readings, kept apart from Quorus's own pause (limit-status rev 3).

A *reading* is what the vendor told us: the exact reset time and where it came
from. The adapter's ``limited_until`` is the *pause*: when reflexd will next
wake the agent, with our padding and cap. The board shows the first and
schedules on the second, so the padding never reads as the vendor's reset.

``source`` is an id from a fixed list, never the vendor's text: harness stderr
can echo credentials or tool inputs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from quorus.bounded import bounded
from quorus.limit_status import RESET_MAX_AHEAD_S as MAX_RESET_AHEAD_S
from quorus.limit_status import SOURCE_KIND  # the relay validates against the same ids

LIMIT_DEFAULT_S = 30 * 60  # no reset time in the message: our guess
LIMIT_MAX_S = 24 * 3600  # the longest we pause before trying again

_RETRY_RE = re.compile(r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?",
                       re.IGNORECASE)
_AT_RE = re.compile(r"try again at\s+(\d{1,2}):(\d{2})\s*([AP]M)?", re.IGNORECASE)


@dataclass(frozen=True)
class LimitReading:
    window: str  # "limit" (a limit message) | "5h" / "7d" (reported usage)
    kind: str  # "reported" | "observed" | "guess"
    source: str  # a SOURCE_KIND key
    seen_at: float  # epoch seconds
    resets_at: float | None = None  # epoch seconds, exact (no padding, no cap)
    used_pct: float | None = None  # reported usage only, 0..100


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


# ── reported usage (slice 3) ────────────────────────────────────────────
#
# Spike 2026-10-21: headless `claude -p --output-format stream-json --verbose`
# (2.1.304) prints {"type": "rate_limit_event", "rate_limit_info": {...}} with
# "unifiedWindows": {"five_hour": {"utilization": 0.75, "resetsAt": <epoch s>},
# "seven_day": {...}}; utilization is a 0..1 fraction. `codex exec --json`
# (0.160.1) carries token usage only, so Codex stays observed / guess.

USAGE_STALE_S = 15 * 60  # older usage is shown with its age
USAGE_MAX_AGE_S = 3600  # older usage is dropped
_CLAUDE_WINDOWS = {"five_hour": "5h", "seven_day": "7d"}


def _window_reading(raw: object, window: str, now: float) -> LimitReading | None:
    """One ``{"utilization", "resetsAt"}`` window, or None if it's malformed."""
    if not isinstance(raw, dict):
        return None
    used, reset = bounded(raw.get("utilization"), 0, 1), raw.get("resetsAt")
    if used is None:
        return None
    if reset is not None:
        if isinstance(reset, bool) or not isinstance(reset, (int, float)):
            return None
        reset = bounded(reset, now, now + MAX_RESET_AHEAD_S)
        if reset is not None and reset <= now:
            reset = None  # a reset we can't trust: keep the usage, drop the time
    return LimitReading(window, "reported", "claude.stream", now, reset, round(used * 100, 1))


def read_claude_usage(stdout: str, now: float) -> dict[str, LimitReading] | None:
    """The 5h / 7d usage in the last ``rate_limit_event`` of a Claude stream.

    None means the stream had no such event: keep what we knew. A dict, even an
    empty one, is the vendor's latest snapshot and replaces it whole, so a
    window it no longer reports (or reports malformed) is gone, not carried
    over. Lines that aren't JSON are skipped, never raised. Only the numbers
    are kept: no other field of the event."""
    for line in reversed((stdout or "").splitlines()):
        if '"rate_limit_event"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "rate_limit_event":
            continue
        info = event.get("rate_limit_info")
        windows = info.get("unifiedWindows") if isinstance(info, dict) else None
        if not isinstance(windows, dict):
            return {}
        out = {}
        for name, window in _CLAUDE_WINDOWS.items():
            reading = _window_reading(windows.get(name), window, now)
            if reading is not None:
                out[window] = reading
        return out
    return None


def live_usage(usage: dict[str, LimitReading], now: float) -> dict[str, LimitReading]:
    """Usage still worth showing: under an hour old and before its reset.
    After a reset, usage is unknown, not 0%."""
    return {w: r for w, r in usage.items()
            if r.seen_at >= now - USAGE_MAX_AGE_S
            and (r.resets_at is None or r.resets_at > now)}
