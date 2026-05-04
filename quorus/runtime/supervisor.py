"""Supervisor — multi-agent reflexd process manager.

Owns N reflexd subprocesses, one per agent participant ({participant}-claude,
{participant}-codex, {participant}-gemini, {participant}-cursor), so a single
``quorus reflexd-manager start`` brings up the whole notification swarm and
keeps it alive across reboots (when paired with the launchd plist generator
in :mod:`quorus.runtime.launchd`).

State file at ``~/.quorus/runtime/supervisor.json`` records ``{participant,
pid, started_at, restart_count}`` per child so the watchdog survives a
supervisor restart and ``status`` queries don't need to ``ps`` the universe.

Watchdog policy: every 30s, poll each child PID. Restart any dead child up
to 5 times in any 10-minute sliding window; after that, mark crash-looping
and refuse to keep restarting (operator must intervene via ``restart``).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("quorus.runtime.supervisor")

DEFAULT_RUNTIME_DIR = Path.home() / ".quorus" / "runtime"
STATE_FILE_NAME = "supervisor.json"
PID_FILE_NAME = "supervisor.pid"
STOP_TERM_GRACE_S = 10.0
WATCHDOG_INTERVAL_S = 30.0
RESTART_WINDOW_S = 600.0  # 10 minutes
RESTART_BUDGET = 5  # max restarts per child per window


# ---------------------------------------------------------------------------
# Child record — what we persist per supervised reflexd process
# ---------------------------------------------------------------------------


@dataclass
class ChildRecord:
    """One reflexd subprocess under supervision."""

    participant: str
    pid: int
    started_at: float
    restart_count: int = 0
    restart_history: list[float] = field(default_factory=list)
    crash_looping: bool = False

    def to_dict(self) -> dict:
        return {
            "participant": self.participant,
            "pid": self.pid,
            "started_at": self.started_at,
            "restart_count": self.restart_count,
            "restart_history": list(self.restart_history),
            "crash_looping": self.crash_looping,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChildRecord":
        return cls(
            participant=str(data.get("participant", "")),
            pid=int(data.get("pid", 0)),
            started_at=float(data.get("started_at", 0.0)),
            restart_count=int(data.get("restart_count", 0)),
            restart_history=[float(x) for x in data.get("restart_history", [])],
            crash_looping=bool(data.get("crash_looping", False)),
        )


# ---------------------------------------------------------------------------
# Supervisor — public API
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _reflexd_script_path() -> Path:
    """Locate ``scripts/reflexd.py`` from this checkout."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "scripts" / "reflexd.py"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("scripts/reflexd.py not found")


class Supervisor:
    """Manages reflexd children for ``participants``."""

    def __init__(
        self,
        participants: Iterable[str],
        *,
        api_keys: dict[str, str] | None = None,
        relay_url: str | None = None,
        runtime_dir: Path | None = None,
        popen: object = None,  # injectable for tests
    ) -> None:
        self.participants = list(dict.fromkeys(p for p in participants if p))
        self.api_keys = dict(api_keys or {})
        self.relay_url = relay_url
        self.runtime_dir = runtime_dir or DEFAULT_RUNTIME_DIR
        self._popen = popen or subprocess.Popen
        self._children: dict[str, ChildRecord] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._watchdog: threading.Thread | None = None

    # ── state persistence ──────────────────────────────────────────────────

    @property
    def state_path(self) -> Path:
        return self.runtime_dir / STATE_FILE_NAME

    def _persist(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        payload = {p: rec.to_dict() for p, rec in self._children.items()}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        for p, rec in data.items():
            if isinstance(rec, dict):
                self._children[p] = ChildRecord.from_dict(rec)

    # ── child lifecycle ────────────────────────────────────────────────────

    def _spawn_child(self, participant: str) -> ChildRecord | None:
        """Launch one reflexd subprocess. Returns the record or None on failure."""
        api_key = self.api_keys.get(participant) or os.environ.get(
            "REFLEXD_API_KEY", ""
        )
        env = os.environ.copy()
        env["REFLEXD_PARTICIPANT"] = participant
        if api_key:
            env["REFLEXD_API_KEY"] = api_key
        if self.relay_url:
            env["REFLEXD_RELAY_URL"] = self.relay_url
        try:
            script = _reflexd_script_path()
        except FileNotFoundError:
            logger.error("reflexd script missing; cannot spawn %s", participant)
            return None
        cmd = [sys.executable, str(script), "start", "--participant", participant]
        log_path = self.runtime_dir / f"reflexd.{participant}.log"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            log_fp = open(log_path, "ab", buffering=0)
        except OSError as exc:
            logger.error("cannot open %s: %s", log_path, exc)
            return None
        try:
            proc = self._popen(  # type: ignore[operator]
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_fp,
                stderr=log_fp,
                start_new_session=True,
                close_fds=True,
            )
        except (OSError, FileNotFoundError) as exc:
            logger.error("failed to spawn reflexd for %s: %s", participant, exc)
            log_fp.close()
            return None
        finally:
            # L5: closing log_fp here is correct. ``close_fds=True`` plus
            # ``stdout=log_fp`` causes Popen to ``dup2`` the log file
            # descriptor onto the child's stdout/stderr; the child holds
            # the only inherited copy. Closing our parent-side fd here
            # (a) prevents the child holding the dir busy on shutdown,
            # (b) ensures EOF is observed when the child exits, and
            # (c) the dup'd fd in the child stays open via the kernel's
            # per-process fd table.
            try:
                log_fp.close()
            except OSError as close_exc:
                # Don't raise — the child has its dup'd fd; our parent fd
                # going away is best-effort cleanup.
                logger.debug(
                    "failed to close parent-side log fp for %s: %s",
                    participant, close_exc,
                )
        return ChildRecord(
            participant=participant,
            pid=int(getattr(proc, "pid", 0)),
            started_at=time.time(),
        )

    def start_all(self) -> dict[str, int]:
        """Spawn one reflexd subprocess per participant. Returns ``{name: pid}``."""
        with self._lock:
            self._load()
            spawned: dict[str, int] = {}
            for p in self.participants:
                existing = self._children.get(p)
                if existing and _pid_alive(existing.pid):
                    spawned[p] = existing.pid
                    continue
                rec = self._spawn_child(p)
                if rec is None:
                    continue
                self._children[p] = rec
                spawned[p] = rec.pid
            self._persist()
            return spawned

    def stop_all(self, *, grace_s: float = STOP_TERM_GRACE_S) -> None:
        """SIGTERM each child, escalate SIGKILL after ``grace_s``."""
        with self._lock:
            self._load()
            self._stop_event.set()
            for p, rec in list(self._children.items()):
                if not _pid_alive(rec.pid):
                    continue
                try:
                    os.kill(rec.pid, signal.SIGTERM)
                except OSError:
                    continue
            deadline = time.time() + grace_s
            while time.time() < deadline:
                if not any(_pid_alive(r.pid) for r in self._children.values()):
                    break
                time.sleep(0.1)
            for rec in self._children.values():
                if _pid_alive(rec.pid):
                    try:
                        os.kill(rec.pid, signal.SIGKILL)
                    except OSError:
                        pass
            self._children.clear()
            try:
                self.state_path.unlink()
            except OSError:
                pass

    def health(self) -> dict[str, str]:
        """Return ``{participant: alive|dead|unknown}`` for each child."""
        with self._lock:
            self._load()
            out: dict[str, str] = {}
            for p in self.participants or list(self._children.keys()):
                rec = self._children.get(p)
                if rec is None:
                    out[p] = "unknown"
                else:
                    out[p] = "alive" if _pid_alive(rec.pid) else "dead"
            return out

    # ── watchdog ───────────────────────────────────────────────────────────

    def _can_restart(self, rec: ChildRecord) -> bool:
        cutoff = time.time() - RESTART_WINDOW_S
        rec.restart_history = [t for t in rec.restart_history if t >= cutoff]
        return len(rec.restart_history) < RESTART_BUDGET

    def _watchdog_tick(self) -> None:
        """One pass of the watchdog. Public-ish for tests."""
        with self._lock:
            for p, rec in list(self._children.items()):
                if rec.crash_looping or _pid_alive(rec.pid):
                    continue
                if not self._can_restart(rec):
                    rec.crash_looping = True
                    logger.error(
                        "crash-loop budget exhausted for %s — refusing further restarts",
                        p,
                    )
                    continue
                logger.warning("restarting dead child %s (pid was %s)", p, rec.pid)
                new_rec = self._spawn_child(p)
                if new_rec is None:
                    continue
                rec.restart_history.append(time.time())
                rec.restart_count += 1
                rec.pid = new_rec.pid
                rec.started_at = new_rec.started_at
            self._persist()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._watchdog_tick()
            except Exception:
                logger.exception("watchdog tick crashed")
            self._stop_event.wait(WATCHDOG_INTERVAL_S)

    def start_watchdog(self) -> None:
        """Spin the background watchdog thread (idempotent)."""
        if self._watchdog and self._watchdog.is_alive():
            return
        self._stop_event.clear()
        self._watchdog = threading.Thread(
            target=self._watchdog_loop,
            name="reflexd-supervisor-watchdog",
            daemon=True,
        )
        self._watchdog.start()

    def stop_watchdog(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=timeout)
