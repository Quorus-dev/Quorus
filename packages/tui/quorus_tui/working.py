"""Who is working right now: agents show presence ``busy`` for the room while
a wake runs (reflexd ``_set_presence``).

Before 2026-10-17 the chat went silent between a request and the reply, often
for minutes, and a working agent looked exactly like a dead one.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import httpx
from rich.text import Text

POLL_S = 3.0


class WorkingTracker:
    """Thread-safe ``name -> busy since`` for the selected room."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._since: dict[str, float] = {}
        self._activity: dict[str, str] = {}
        self._presence: list[dict[str, Any]] | None = None

    def update(self, busy: set[str], now: float | None = None,
               activity: dict[str, str] | None = None) -> list[str]:
        """Replace the busy set; return the names that just started."""
        now = time.time() if now is None else now
        with self._lock:
            started = sorted(busy - self._since.keys())
            self._since = {n: self._since.get(n, now) for n in busy}
            self._activity = {n: a for n, a in (activity or {}).items() if n in busy and a}
        return started

    def snapshot(self) -> list[tuple[str, float]]:
        with self._lock:
            return sorted(self._since.items(), key=lambda kv: kv[1])

    def set_presence(self, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            self._presence = list(rows)

    def presence(self) -> list[dict[str, Any]] | None:
        """The last ``/presence`` rows; None until the first fetch succeeds."""
        with self._lock:
            return self._presence

    def activity(self) -> dict[str, str]:
        """``name -> current step`` ("editing relay.py") for busy agents."""
        with self._lock:
            return dict(self._activity)


def busy_in_room(presence: list[dict[str, Any]], room: str) -> set[str]:
    return {p.get("name", "") for p in presence
            if p.get("online") and p.get("status") == "busy" and p.get("room") == room
            and p.get("name")}


def activity_in_room(presence: list[dict[str, Any]], room: str) -> dict[str, str]:
    return {p["name"]: str(p.get("activity") or "") for p in presence
            if p.get("name") in busy_in_room([p], room) and p.get("activity")}


def fetch_presence(relay: str, headers: dict[str, str]) -> list[dict[str, Any]] | None:
    try:
        resp = httpx.get(f"{relay.rstrip('/')}/presence", headers=headers, timeout=5)
        data = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"


def render_working_line(snapshot: list[tuple[str, float]], now: float | None = None,
                        activity: dict[str, str] | None = None) -> Text | None:
    """``  ⋯ @a-claude editing relay.py 1m 20s · @a-codex working 12s``"""
    if not snapshot:
        return None
    now = time.time() if now is None else now
    line = Text("  ⋯ ", style="dim")
    for i, (name, since) in enumerate(snapshot):
        if i:
            line.append(" · ", style="dim")
        line.append(f"@{name}", style="bold accent")
        step = (activity or {}).get(name) or "working"
        line.append(f" {step} {_elapsed(now - since)}", style="muted italic")
    return line


def presence_loop(
    relay: str, headers: dict[str, str], tracker: WorkingTracker,
    selected_room: Callable[[], str], announce: Callable[[str], None],
    stop: threading.Event, interval: float = POLL_S,
) -> None:
    """Poll presence; ``announce(name)`` once when an agent starts working in
    the selected room. Never raises: presence is a nicety, chat must not die."""
    last_room = None
    while not stop.is_set():
        try:
            room = selected_room()
            presence = fetch_presence(relay, headers) if room else []
            if presence is not None:
                if room:
                    tracker.set_presence(presence)
                busy = busy_in_room(presence, room) if room else set()
                steps = activity_in_room(presence, room) if room else {}
                if room != last_room:  # switching rooms is not "started working"
                    tracker.update(busy, activity=steps)
                    last_room = room
                else:
                    for name in tracker.update(busy, activity=steps):
                        announce(name)
        except Exception:
            pass
        stop.wait(interval)
