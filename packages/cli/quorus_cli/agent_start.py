"""Did the daemon `quorus agent add` just started actually stay up?

`agent add` used to print "@x is running" whatever happened: launchctl's exit
code was ignored and nothing checked for a live process, so a daemon that
crashed on start looked healthy (take-3 bug 1, docs/plans/take3-fixes.md).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from quorus.observability.redaction import env_secret_values, redact_secrets

# How long to wait for a live daemon, and how long one pid must stay up to
# count: launchd's KeepAlive restarts a crashing job, so a pid seen once can be
# a crash loop.
WAIT_S = 5.0
SETTLE_S = 1.5
TAIL_LINES = 10


def stays_up(pid_of: Callable[[], int | None], *, wait_s: float | None = None,
             settle_s: float | None = None, poll_s: float = 0.2) -> bool:
    """True once the same pid has been live for *settle_s*, within *wait_s*."""
    wait_s = WAIT_S if wait_s is None else wait_s
    settle_s = SETTLE_S if settle_s is None else settle_s
    deadline = time.monotonic() + wait_s
    seen: int | None = None
    since = time.monotonic()
    while True:
        pid, now = pid_of(), time.monotonic()
        if pid is None or pid != seen:
            seen, since = pid, now
        if pid is not None and now - since >= settle_s:
            return True
        if now >= deadline:
            return False
        time.sleep(poll_s)


def size(path: Path) -> int:
    """Where this start's output begins: the crash log is appended to."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def log_tail(path: Path, offset: int, known: Iterable[str] = ()) -> str:
    """The last lines *path* gained after *offset*, credentials masked."""
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [ln for ln in text.splitlines() if ln.strip()][-TAIL_LINES:]
    secrets = [*known, *env_secret_values(os.environ)]
    return "\n".join(f"  {redact_secrets(ln, secrets)}" for ln in lines)


def not_started(name: str, log: Path, offset: int, known: Iterable[str]) -> SystemExit:
    tail = log_tail(log, offset, known)
    if tail:
        return SystemExit(f"agent: @{name} didn't stay up; its last log lines:\n{tail}")
    return SystemExit(f"agent: @{name} didn't stay up, and it logged nothing to {log}")
