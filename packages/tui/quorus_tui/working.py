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

    def update(self, busy: set[str], now: float | None = None) -> list[str]:
        """Replace the busy set; return the names that just started."""
        now = time.time() if now is None else now
        with self._lock:
            started = sorted(busy - self._since.keys())
            self._since = {n: self._since.get(n, now) for n in busy}
        return started

    def snapshot(self) -> list[tuple[str, float]]:
        with self._lock:
            return sorted(self._since.items(), key=lambda kv: kv[1])


def busy_in_room(presence: list[dict[str, Any]], room: str) -> set[str]:
    return {p.get("name", "") for p in presence
            if p.get("online") and p.get("status") == "busy" and p.get("room") == room
            and p.get("name")}


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


def render_working_line(snapshot: list[tuple[str, float]], now: float | None = None) -> Text | None:
    """``  ⋯ @a-claude working 1m 20s · @a-codex working 12s``"""
    if not snapshot:
        return None
    now = time.time() if now is None else now
    line = Text("  ⋯ ", style="dim")
    for i, (name, since) in enumerate(snapshot):
        if i:
            line.append(" · ", style="dim")
        line.append(f"@{name}", style="bold accent")
        line.append(f" working {_elapsed(now - since)}", style="muted italic")
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
                busy = busy_in_room(presence, room) if room else set()
                if room != last_room:  # switching rooms is not "started working"
                    tracker.update(busy)
                    last_room = room
                else:
                    for name in tracker.update(busy):
                        announce(name)
        except Exception:
            pass
        stop.wait(interval)
