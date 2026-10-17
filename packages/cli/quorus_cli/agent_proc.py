"""Wake daemons without launchd (Linux, WSL, BSD): a detached process that
leads its own session, tracked by ``<agents dir>/<name>.pid``.

Never tested on Linux before 2026-10-16: `quorus agent remove` crashed on the
missing `launchctl` and left the daemon running, and `quorus agent list`
showed nothing because it only asked launchd.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


def pidfile(agents_dir: Path, name: str) -> Path:
    return agents_dir / f"{name}.pid"


def _read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, but another user's: not our daemon
        return False
    return True


def _is_daemon_for(pid: int, name: str) -> bool:
    """A live reflexd for *name*, not whatever reused the pid after a reboot
    (signalling a stale pid could kill an unrelated program)."""
    if not _alive(pid):
        return False
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(
            "utf-8", "replace").split("\0")
    except OSError:  # no /proc (macOS, BSD)
        try:
            argv = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                  capture_output=True, text=True, timeout=10,
                                  check=False).stdout.split()
        except (OSError, subprocess.SubprocessError):
            return False  # can't tell: never signal a process we can't vouch for
    return any(a.endswith("reflexd.py") for a in argv) and any(
        a == "--participant" and nxt == name for a, nxt in zip(argv, argv[1:]))


def live_pid(agents_dir: Path, name: str) -> int | None:
    pid = _read_pid(pidfile(agents_dir, name))
    return pid if pid is not None and _is_daemon_for(pid, name) else None


def stop(agents_dir: Path, name: str, timeout: float = 10.0) -> bool:
    """Stop *name*'s daemon and everything it started. True if one was running.

    SIGTERM goes to the daemon alone: it kills the agent CLI it is running
    and keeps the interrupted message queued. Signalling the whole group at
    once let the CLI die first, which reads as a failed wake. Then SIGKILL
    the session's process group for anything left behind (an MCP server or
    language server the agent CLI started outlives a killed parent).
    """
    path = pidfile(agents_dir, name)
    pid = live_pid(agents_dir, name)
    path.unlink(missing_ok=True)
    if pid is None:
        return False
    try:
        own_group = os.getpgid(pid) == pid  # started with start_new_session
    except OSError:
        own_group = False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    try:
        if own_group:
            os.killpg(pid, signal.SIGKILL)
        elif _alive(pid):
            os.kill(pid, signal.SIGKILL)
    except OSError:  # ESRCH: nothing left
        pass
    return True
