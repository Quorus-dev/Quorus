"""Live Claude Code session registry (WAKE_REBUILD_SPEC L1).

Records the inbox-socket coordinates of running Claude Code sessions so
reflexd can PUSH an @-mention into an already-open interactive session
(no keystrokes, mid-turn) instead of cold-spawning a headless one.

A ``SessionStart`` hook calls ``quorus session-register`` which writes an
entry keyed by PID into ~/.quorus/live-sessions.json (0600); ``SessionEnd``
calls ``quorus session-unregister``. Stale entries (dead PIDs) are pruned on
every read, so a crash without SessionEnd self-heals.

The daemon reads this file, filters by cwd (matching a room's bound
workspace) and liveness. Delivery itself is the Stop hook's job.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path.home() / ".quorus" / "live-sessions.json"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — never our own sessions, but
        # treat as alive rather than drop a real entry.
        return True
    except OSError:
        return False
    return True


def _load() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(REGISTRY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _prune(data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        pid: entry for pid, entry in data.items()
        if str(pid).isdigit() and _pid_alive(int(pid))
    }


def register(
    *, pid: int, cwd: str, participant: str | None = None,
    has_socket: bool = False,
) -> None:
    """Record a live session.

    Deliberately stores NO socket path and NO messaging token: delivery to
    live sessions goes through the documented Stop hook, not the raw inbox
    socket (whose message-frame schema Anthropic has not published as of
    2026-08). Keeping the secret out of this file removes a credential at
    rest for zero functional loss. ``has_socket`` records only whether the
    session *could* be injected into, for future use.
    """
    data = _prune(_load())
    data[str(pid)] = {
        "pid": pid,
        "cwd": cwd,
        "participant": participant or "",
        "has_socket": bool(has_socket),
    }
    _save(data)


def unregister(pid: int) -> None:
    data = _load()
    if str(pid) in data:
        del data[str(pid)]
        _save(_prune(data))


def find_for_cwd(cwd: str) -> dict[str, Any] | None:
    """Return a live session whose cwd matches (exact, then parent-of)."""
    data = _prune(_load())
    target = Path(cwd).resolve()
    # Exact cwd match wins; else a session running inside the workspace tree.
    exact = [e for e in data.values() if Path(e["cwd"]).resolve() == target]
    if exact:
        return exact[0]
    for e in data.values():
        try:
            ecwd = Path(e["cwd"]).resolve()
        except (OSError, ValueError):
            continue
        if ecwd == target or target in ecwd.parents or ecwd in target.parents:
            return e
    return None


def list_live() -> list[dict[str, Any]]:
    data = _prune(_load())
    _save(data)  # persist the prune
    return sorted(data.values(), key=lambda e: e.get("pid", 0))
