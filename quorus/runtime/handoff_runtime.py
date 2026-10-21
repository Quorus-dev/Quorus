"""Local configuration and fail-closed guards shared by handoff wiring."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from quorus.runtime.check_config import CheckConfig
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_ownership import fenced
from quorus.runtime.handoff_state import Journal, StateError
from quorus.runtime.worktree_hold import marker_path


def journal(daemon, room, worktree) -> Journal:
    name = hashlib.sha256(daemon.config.participant_name.encode()).hexdigest()[:24]
    return Journal(daemon.config.runtime_dir / f"handoffs-{name}.json", room,
                   worktree.repo, hold_dir=daemon._unfinished_dir)


def config(bindings: Path | None, room: str, repo: Path) -> CheckConfig | None:
    if bindings is None:
        return None
    value = json.loads(bindings.read_text()).get(room)
    if not isinstance(value, dict) or Path(value.get("path", "")).resolve() != repo.resolve():
        return None
    return CheckConfig.from_record(value["check"]) if value.get("check") else None


def check_env() -> dict[str, str]:
    # Owner-configured checks do not inherit daemon/agent relay or vendor secrets.
    # An allowlist also excludes GIT_CONFIG_*, Python injection and loader variables.
    keys = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}
    return {k: v for k, v in os.environ.items() if k in keys}


def skip_sync(daemon, room, worktree) -> bool:
    """Never sync a held receiver or a durable sender before its authority gate."""
    if marker_path(daemon._unfinished_dir, worktree.path).exists():
        return True
    store = journal(daemon, room, worktree)
    try:
        with store.locked():
            return any(e["state"] in {"sent", "received", "adopting", "adopted", "verified",
                                      "continuing"}
                       and str(e.get("scope", "")).endswith(f":{store.repo}")
                       for e in store._load().values())
    except (OSError, ValueError):
        return True


def blocked(daemon, room, worktree) -> bool:
    """Quota returning and unrelated asks cannot release transferred ownership."""
    try:
        store = journal(daemon, room, worktree)
        if fenced(store, Git(worktree.path), worktree.main):
            return True
        path = marker_path(daemon._unfinished_dir, worktree.path)
        if path.exists():
            value = json.loads(path.read_text())
            return not isinstance(value, dict) or "handoff" in value
        return False
    except (OSError, ValueError, StateError):
        return True
