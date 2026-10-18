""""While you were away": what your agents did since you last opened Quorus.

Agents run around the clock, so the first question on coming back is what
shipped, who needs me, and who is stuck. Before 2026-10-17 the answer meant
scrolling every room.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text

from quorus.notifications.agent_events import asks_owner

PUBLISHED = "(quorus) published "
_STUCK = ("⚠", "[reflexd] harness")


@dataclass
class RoomDigest:
    room: str
    messages: int = 0
    shipped: int = 0
    needs_you: list[tuple[str, str]] = field(default_factory=list)  # (agent, line)
    stuck: list[str] = field(default_factory=list)  # agent names

    @property
    def notable(self) -> bool:
        return bool(self.shipped or self.needs_you or self.stuck)


def _ts(raw: object) -> float:
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip() and not line.startswith("(quorus)"):
            return line.strip()
    return text.strip()


def room_digest(room: str, history: list[dict], *, me: str, since: float) -> RoomDigest:
    d = RoomDigest(room)
    for m in history:
        sender = m.get("from_name") or ""
        if (sender == me or m.get("message_type") == "status"
                or _ts(m.get("timestamp")) <= since):
            continue
        body = m.get("content") or ""
        d.messages += 1
        for ln in body.splitlines():  # "(quorus) published 2 commit(s) from …"
            if ln.startswith(PUBLISHED):
                n = re.match(r"(\d+)", ln[len(PUBLISHED):])
                d.shipped += int(n.group(1)) if n else 1
        if body.startswith(_STUCK) and sender not in d.stuck:
            d.stuck.append(sender)
        elif (ask := asks_owner(body, me)) is not None:
            d.needs_you.append((sender, ask))
    return d


def load_last_seen(path: Path) -> float | None:
    try:
        return float(json.loads(path.read_text(encoding="utf-8"))["last_seen"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_last_seen(path: Path, when: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_seen": when}), encoding="utf-8")
    except OSError:
        pass


def render(digests: list[RoomDigest], *, indent: str = "  ", width: int = 78) -> list[Text]:
    """Rows for the home screen; [] when nothing notable happened."""
    notable = [d for d in digests if d.notable]
    if not notable:
        return []
    rows = [Text(f"{indent}While you were away", style="bold primary")]
    for d in notable:
        row = Text(f"{indent}  #{d.room}  ", style="accent")
        parts = []
        if d.shipped:
            plural = "commit" if d.shipped == 1 else "commits"
            parts.append((f"{d.shipped} {plural} shipped to main", "success"))
        if d.needs_you:
            n = len(d.needs_you)
            parts.append((f"{n} {'needs' if n == 1 else 'need'} you", "warning"))
        if d.stuck:
            parts.append((f"stuck: {', '.join('@' + s for s in d.stuck)}", "error"))
        parts.append((f"{d.messages} new", "muted"))
        for i, (label, style) in enumerate(parts):
            if i:
                row.append(" · ", style="dim")
            row.append(label, style=style)
        rows.append(row)
        for agent, line in d.needs_you[:3]:
            budget = max(10, width - len(indent) - len(agent) - 8)
            short = line if len(line) <= budget else line[: budget - 1] + "…"
            rows.append(Text(f"{indent}    @{agent}: {short}", style="muted"))
    return rows
