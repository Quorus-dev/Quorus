"""One line per agent about its limits, plus the pre-wall hint (limit-status slice 4).

Built by the relay from validated presence (``quorus.limit_status.clean``) so the
board and ``quorus ps`` say the same thing. Times are relative ("resets in 2h
10m"): the relay's clock zone isn't the reader's (laptop 2 in UTC told a room in
Toronto "until about Sat 04:33", 2026-10-17).

Rules (design rev 3): the vendor's reset is never our retry time; a guess always
carries "?"; a Quorus cooldown is never called quota; usage older than 15
minutes shows its age; nothing known is said as nothing known, never 0%.
"""

from __future__ import annotations

from typing import Any

USAGE_STALE_S = 15 * 60
PREWALL_PCT = 90.0
WINDOW_NAMES = {"5h": "5-hour window", "7d": "7-day window"}
SOURCE_SAYS = {
    "claude.statusline": "Claude Code reported",
    "claude.stream": "Claude Code reported",
    "codex.token_count": "Codex reported",
    "msg.retry_in": 'vendor said "retry in …"',
    "msg.try_again_at": 'vendor said "try again at …"',
}
PAUSE_WHY = {"slow_start": "free model slow to start", "no_route": "free model unavailable"}


def duration(seconds: float) -> str:
    """``45m``, ``2h 10m``, ``3d 4h``; under a minute is ``<1m``."""
    minutes = int(max(0.0, seconds) // 60)
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def _usage_line(usage: list[dict[str, Any]], now: float) -> str:
    # Closest to the wall first: highest use, then the earlier reset.
    usage = sorted(usage, key=lambda r: (-r["used_pct"], r["resets_at"] or float("inf")))
    top = usage[0]
    line = f"{WINDOW_NAMES[top['window']]} {top['used_pct']:.0f}% used"
    if top["resets_at"]:
        line += f" · resets in {duration(top['resets_at'] - now)}"
    for other in usage[1:]:
        line += f" · {other['window']} {other['used_pct']:.0f}%"
    age = now - top["seen_at"]
    said = SOURCE_SAYS.get(top["source"], "reported")
    return line + (f" ({said}, {duration(age)} ago)" if age > USAGE_STALE_S else f" ({said})")


def describe(limits: dict[str, Any], now: float) -> str:
    """The limit line for one agent's cleaned limits; ``""`` when nothing is known."""
    pause = limits.get("pause")
    readings = {r["window"]: r for r in limits.get("readings") or []}
    limit = readings.get("limit")
    if limit is not None and limit["resets_at"]:
        return (f"out of quota · resets in {duration(limit['resets_at'] - now)} "
                f"({SOURCE_SAYS[limit['source']]})")
    if pause is not None and pause["reason"] == "quota":
        return (f"out of quota · retry in about {duration(pause['until'] - now)}? "
                "(no reset time given)")
    if pause is not None:
        return (f"paused by Quorus · retry in {duration(pause['until'] - now)} "
                f"({PAUSE_WHY.get(pause['reason'], 'cooling down')})")
    usage = [r for w, r in readings.items() if w in WINDOW_NAMES]
    return _usage_line(usage, now) if usage else ""


def _vendor(limits: dict[str, Any]) -> str:
    """Whose CLI reported this agent's usage ("claude", "codex"), or ""."""
    for r in limits.get("readings") or []:
        if r["kind"] == "reported":
            return r["source"].split(".", 1)[0]
    return ""


def _has_room(limits: dict[str, Any]) -> bool:
    """Known room only: fresh reported usage under the line on every window it
    reports, and no limit or pause. No data, a guess, or stale usage is unknown,
    never room."""
    if limits.get("pause"):
        return False
    readings = limits.get("readings") or []
    usage = [r for r in readings if r["kind"] == "reported"]
    return bool(usage) and len(usage) == len(readings) and all(
        r["used_pct"] < PREWALL_PCT for r in usage)


def prewall_hints(agents: list[dict[str, Any]], now: float) -> dict[str, str]:
    """``name -> "next task to @x?"`` for an agent at 90%+ of a reported window
    when a teammate in its room, on another vendor, is known to have room.
    A suggestion only: it routes nothing.

    ``agents`` items: ``{"name", "room", "limits"}`` for online agents."""
    hints: dict[str, str] = {}
    for a in agents:
        mine, vendor = a["limits"], _vendor(a["limits"])
        hot = [r for r in mine.get("readings") or []
               if r["kind"] == "reported" and r["used_pct"] >= PREWALL_PCT
               and now - r["seen_at"] <= USAGE_STALE_S]
        if not hot or not a.get("room"):
            continue
        for b in sorted(agents, key=lambda x: x["name"]):
            if (b["name"] != a["name"] and b.get("room") == a["room"]
                    and _vendor(b["limits"]) not in ("", vendor) and _has_room(b["limits"])
                    and all(now - r["seen_at"] <= USAGE_STALE_S
                            for r in b["limits"].get("readings") or [])):
                hints[a["name"]] = f"next task to @{b['name']}?"
                break
    return hints
