"""Per-user tuning: what this user's Quorus changed about itself, and why.

Each install personalizes itself from its owner's own outcomes.
Every change is an *adaptation*:

* private: it lives in ``~/.quorus/tuning.json`` (0600), one file per user
  whatever ``QUORUS_CONFIG_DIR`` an agent runs with, and is never sent to the
  relay; only a one-line announcement with counts is posted to the room;
* scoped: each entry names the relay, owner and repo it was learned on;
* reversible: ``quorus tune undo <id>`` stops one until ``quorus tune redo
  <id>``; ``quorus tune off`` stops all of them at once.

Several agent daemons write this file, so every read-modify-write holds an
exclusive lock on a sibling ``.lock`` file, and writes are atomic.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from quorus.private_files import write_private_json

try:  # POSIX only; Windows daemons run without the cross-process lock.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None  # type: ignore[assignment]

KINDS = ("bid",)
STATES = ("active", "undone", "retired")
MAX_TEXT = 200


@dataclass
class Adaptation:
    id: str
    kind: str
    subject: str  # who it applies to, e.g. the agent whose bids moved
    scope: str  # "relay|owner|repo" it was learned on
    change: str
    because: str
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    state: str = "active"

    @property
    def created_ts(self) -> float:
        try:
            return datetime.fromisoformat(self.created_at).timestamp()
        except ValueError:
            return 0.0


def path() -> Path:
    override = os.environ.get("QUORUS_TUNING_FILE", "").strip()
    return Path(override) if override else Path.home() / ".quorus" / "tuning.json"


def _empty() -> dict[str, Any]:
    return {"enabled": True, "next": 1, "adaptations": []}


def _invalid() -> dict[str, Any]:
    # Unreadable state must fail closed: stock behaviour, and nothing is
    # written over it, so a damaged `tune off` can't turn adapting back on.
    return {"enabled": False, "next": 1, "adaptations": [], "invalid": True}


def _read(p: Path) -> dict[str, Any]:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError):
        return _invalid()
    if not isinstance(data, dict) or not isinstance(data.get("adaptations"), list) \
            or not isinstance(data.get("enabled", True), bool):
        return _invalid()
    data.setdefault("enabled", True)
    data.setdefault("next", len(data["adaptations"]) + 1)
    return data


@contextlib.contextmanager
def _locked(repair: bool = False) -> Iterator[dict[str, Any]]:
    """Read-modify-write under the lock. An unreadable file is only replaced
    when the user asks (``repair``: ``tune on``/``off``); otherwise changes to
    it are dropped."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "a", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = _read(p)
            invalid = data.pop("invalid", False)
            if invalid and repair:
                data = _empty()
            yield data
            if not invalid or repair:
                write_private_json(p, data, indent=2)
        finally:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _load(raw: Any) -> Adaptation | None:
    if not isinstance(raw, dict):
        return None
    try:
        a = Adaptation(**{k: raw[k] for k in Adaptation.__dataclass_fields__ if k in raw})
    except TypeError:
        return None
    return a if a.kind in KINDS and a.state in STATES else None


def _all(data: dict[str, Any]) -> list[Adaptation]:
    return [a for raw in data["adaptations"] if (a := _load(raw))]


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def enabled() -> bool:
    return bool(_read(path()).get("enabled", True))


def is_invalid() -> bool:
    return bool(_read(path()).get("invalid"))


def set_enabled(on: bool) -> None:
    with _locked(repair=True) as data:
        data["enabled"] = bool(on)


def adaptations() -> list[Adaptation]:
    """Every adaptation ever made, oldest first."""
    return _all(_read(path()))


def latest(kind: str, subject: str, scope: str) -> Adaptation | None:
    """The newest adaptation for this kind, subject and scope, in any state."""
    rows = [a for a in adaptations()
            if a.kind == kind and a.subject == subject and a.scope == scope]
    return rows[-1] if rows else None


def adapt(kind: str, *, subject: str, scope: str, change: str, because: str,
          evidence: dict[str, Any] | None = None) -> Adaptation | None:
    """Record a new active adaptation, or None if tuning is off or the newest
    one for this subject and scope is still active or undone (an undo holds
    until ``redo``)."""
    if kind not in KINDS:
        raise ValueError(f"unknown adaptation kind: {kind!r}")
    with _locked() as data:
        if not data.get("enabled", True):
            return None
        mine = [a for a in _all(data)
                if a.kind == kind and a.subject == subject and a.scope == scope]
        if mine and mine[-1].state in ("active", "undone"):
            return None
        new = Adaptation(id=f"t{int(data['next'])}", kind=kind, subject=subject,
                         scope=scope, change=_clip(change), because=_clip(because),
                         evidence=dict(evidence or {}),
                         created_at=datetime.now(timezone.utc).isoformat())
        data["next"] = int(data["next"]) + 1
        data["adaptations"].append(asdict(new))
        return new


def _set_state(adaptation_id: str, frm: str, to: str) -> Adaptation | None:
    with _locked() as data:
        for raw in data["adaptations"]:
            if isinstance(raw, dict) and raw.get("id") == adaptation_id \
                    and raw.get("state") == frm:
                raw["state"] = to
                return _load(raw)
    return None


def undo(adaptation_id: str) -> Adaptation | None:
    """Stop one adaptation; it stays stopped until :func:`redo`."""
    return _set_state(adaptation_id, "active", "undone")


def redo(adaptation_id: str) -> Adaptation | None:
    """Re-enable an undone adaptation."""
    return _set_state(adaptation_id, "undone", "active")


def retire(adaptation_id: str) -> Adaptation | None:
    """The evidence no longer supports it (not the user's decision)."""
    return _set_state(adaptation_id, "active", "retired")


def to_json() -> str:
    data = _read(path())
    return json.dumps({"enabled": bool(data.get("enabled", True)),
                       "invalid": bool(data.get("invalid")),
                       "adaptations": [asdict(a) for a in _all(data)]}, indent=2)
