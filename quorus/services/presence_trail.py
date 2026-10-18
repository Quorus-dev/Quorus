"""Each agent's last few live steps, for the mission-control view (2026-10-18).

Presence carries one ``activity`` string ("editing relay.py"), overwritten on
every heartbeat, so a viewer could only ever see the current step. The trail
keeps the last :data:`TRAIL_MAX` distinct steps with their time. It is built
only from that same field, which the relay already sanitises (printable, 80
chars): never raw harness output, which can carry secrets.
"""

from __future__ import annotations

import json
from typing import Any

TRAIL_MAX = 20


def extend(trail: list[list[Any]] | None, activity: str, now: float) -> list[list[Any]]:
    """*trail* plus ``[now, activity]`` when it's a new step; newest last."""
    out = [list(t) for t in (trail or []) if isinstance(t, (list, tuple)) and len(t) == 2]
    if activity and (not out or out[-1][1] != activity):
        out.append([now, activity])
    return out[-TRAIL_MAX:]


def load(raw: Any) -> list[list[Any]]:
    """A stored trail (a list, or JSON text from Redis) back to a list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, (str, bytes)) and raw:
        try:
            value = json.loads(raw)
        except ValueError:
            return []
        return value if isinstance(value, list) else []
    return []


def public(raw: Any) -> list[dict[str, Any]]:
    """The wire shape: ``[{"t": epoch_seconds, "step": "..."}]``, oldest first."""
    return [{"t": float(t), "step": str(step)} for t, step in load(raw)]
