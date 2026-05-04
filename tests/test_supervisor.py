"""Tests for ``quorus.runtime.supervisor`` and the launchd plist generator.

The supervisor manages N reflexd subprocesses, one per agent participant.
We never spawn a real reflexd here — we inject a fake ``Popen`` that records
argv/env and returns objects with controllable PIDs. The ``_pid_alive`` shim
is patched so the watchdog can be exercised deterministically without
process plumbing.

Coverage:

* ``start_all`` launches one subprocess per participant
* ``stop_all`` SIGTERMs then SIGKILLs hung children
* ``health`` reports alive/dead/unknown per participant
* The watchdog restarts dead children
* The watchdog circuit-breaks after 5 restarts in 10 minutes
* The supervisor pidfile lifecycle (write on start, clear on stop)
* The launchd plist parses as valid XML and runs the right command
"""
from __future__ import annotations

import os
import plistlib
import signal
import subprocess
import time
from pathlib import Path

import pytest

from quorus.runtime import launchd as launchd_mod
from quorus.runtime import supervisor as sup_mod
from quorus.runtime.supervisor import (
    RESTART_BUDGET,
    Supervisor,
)

# ---------------------------------------------------------------------------
# Fake Popen / liveness plumbing — no real subprocesses involved
# ---------------------------------------------------------------------------


class FakeProc:
    """Minimal stand-in for the bits of ``subprocess.Popen`` we touch."""

    def __init__(self, pid: int) -> None:
        self.pid = pid


class FakePopenFactory:
    """Callable factory that mimics ``subprocess.Popen``.

    Records every spawn (cmd + env) and hands back FakeProc objects with
    monotonically-increasing PIDs starting at 50000. Tests can mark PIDs as
    "dead" via ``mark_dead`` so the watchdog sees them as such on next tick.
    """

    def __init__(self, start_pid: int = 50000) -> None:
        self._next_pid = start_pid
        self.calls: list[dict] = []
        self.alive: set[int] = set()

    def __call__(self, cmd, *, env=None, **kwargs):
        pid = self._next_pid
        self._next_pid += 1
        self.alive.add(pid)
        self.calls.append({"cmd": list(cmd), "env": dict(env or {}), "pid": pid})
        return FakeProc(pid)

    def mark_dead(self, pid: int) -> None:
        self.alive.discard(pid)


@pytest.fixture
def runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated runtime dir under a tmp_path."""
    d = tmp_path / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sup_mod, "DEFAULT_RUNTIME_DIR", d)
    return d


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> FakePopenFactory:
    """Inject a fake Popen + alive-set into the supervisor module."""
    factory = FakePopenFactory()

    def _alive(pid: int) -> bool:
        return pid in factory.alive

    monkeypatch.setattr(sup_mod, "_pid_alive", _alive)
    return factory


@pytest.fixture
def fake_kill(monkeypatch: pytest.MonkeyPatch, fake_popen: FakePopenFactory):
    """Capture os.kill so SIGTERM/SIGKILL don't actually fire."""
    captured: list[tuple[int, int]] = []

    def _kill(pid: int, sig: int) -> None:
        captured.append((pid, sig))
        if sig in (signal.SIGTERM, signal.SIGKILL):
            fake_popen.mark_dead(pid)

    monkeypatch.setattr(os, "kill", _kill)
    return captured


# ---------------------------------------------------------------------------
# start_all / stop_all
# ---------------------------------------------------------------------------


def test_start_all_launches_n_subprocesses(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    """Every participant gets one Popen call with its env wired up."""
    sup = Supervisor(
        ["arav-claude", "arav-codex", "arav-gemini"],
        api_keys={
            "arav-claude": "k-claude",
            "arav-codex": "k-codex",
            "arav-gemini": "k-gemini",
        },
        relay_url="https://relay.example",
        runtime_dir=runtime_dir,
        popen=fake_popen,
    )
    spawned = sup.start_all()
    assert sorted(spawned.keys()) == ["arav-claude", "arav-codex", "arav-gemini"]
    assert len(fake_popen.calls) == 3
    for call in fake_popen.calls:
        assert call["env"]["REFLEXD_RELAY_URL"] == "https://relay.example"
        assert "REFLEXD_PARTICIPANT" in call["env"]
        assert call["env"]["REFLEXD_API_KEY"].startswith("k-")
    # State persisted with mode 0600.
    state_path = runtime_dir / "supervisor.json"
    assert state_path.exists()
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_stop_all_sigterms_then_sigkills(
    runtime_dir: Path, fake_popen: FakePopenFactory, fake_kill: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGTERM first; if children stay alive past grace, SIGKILL."""
    sup = Supervisor(
        ["a-claude", "b-codex"], runtime_dir=runtime_dir, popen=fake_popen,
    )
    sup.start_all()
    # Make children hang past grace so we exercise the SIGKILL escalation.
    # Trick: only mark dead on SIGKILL, not SIGTERM.
    fake_kill.clear()

    real_kill = os.kill
    sigterm_seen: list[int] = []

    def _stubborn_kill(pid: int, sig: int) -> None:
        if sig == signal.SIGTERM:
            sigterm_seen.append(pid)
            return  # ignore — child "hangs"
        if sig == signal.SIGKILL:
            fake_popen.mark_dead(pid)
            return
        real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", _stubborn_kill)
    sup.stop_all(grace_s=0.2)

    # All children eventually marked dead.
    for call in fake_popen.calls:
        assert call["pid"] not in fake_popen.alive
    # SIGTERM fired for both children.
    assert sorted(sigterm_seen) == sorted(c["pid"] for c in fake_popen.calls)


def test_health_reports_alive_dead(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    """Health is per-participant alive/dead/unknown."""
    sup = Supervisor(
        ["x-claude", "y-codex"], runtime_dir=runtime_dir, popen=fake_popen,
    )
    sup.start_all()
    h1 = sup.health()
    assert h1["x-claude"] == "alive"
    assert h1["y-codex"] == "alive"

    # Kill one of the PIDs directly and re-check health.
    pid_to_kill = fake_popen.calls[0]["pid"]
    fake_popen.mark_dead(pid_to_kill)
    h2 = sup.health()
    assert h2["x-claude"] == "dead"
    assert h2["y-codex"] == "alive"

    # Unknown participant — never registered.
    sup_unknown = Supervisor(
        ["never-claude"], runtime_dir=runtime_dir, popen=fake_popen,
    )
    assert sup_unknown.health()["never-claude"] == "unknown"


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


def test_watchdog_restarts_dead_child(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    """A dead child gets re-spawned on the next watchdog tick."""
    sup = Supervisor(["w-claude"], runtime_dir=runtime_dir, popen=fake_popen)
    sup.start_all()
    first_pid = fake_popen.calls[0]["pid"]
    assert sup.health()["w-claude"] == "alive"

    fake_popen.mark_dead(first_pid)
    assert sup.health()["w-claude"] == "dead"
    sup._watchdog_tick()

    assert len(fake_popen.calls) == 2
    new_pid = fake_popen.calls[1]["pid"]
    assert new_pid != first_pid
    assert sup.health()["w-claude"] == "alive"
    rec = sup._children["w-claude"]
    assert rec.restart_count == 1
    assert rec.crash_looping is False


def test_watchdog_circuit_breaks_after_5_restarts_in_10_min(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    """After ``RESTART_BUDGET`` restarts in the window, mark crash-looping."""
    sup = Supervisor(["loop-claude"], runtime_dir=runtime_dir, popen=fake_popen)
    sup.start_all()
    rec = sup._children["loop-claude"]

    # Burn through the entire budget. After each tick the child is dead, so
    # the next tick fires another restart — until we hit the budget cap.
    for _ in range(RESTART_BUDGET):
        fake_popen.mark_dead(rec.pid)
        sup._watchdog_tick()

    # One more tick after the budget — should NOT spawn a new process.
    spawn_count_before = len(fake_popen.calls)
    fake_popen.mark_dead(rec.pid)
    sup._watchdog_tick()
    assert len(fake_popen.calls) == spawn_count_before, (
        "circuit breaker should refuse further restarts"
    )
    assert rec.crash_looping is True
    assert rec.restart_count == RESTART_BUDGET


# ---------------------------------------------------------------------------
# Pidfile lifecycle
# ---------------------------------------------------------------------------


def test_supervisor_pidfile_lifecycle(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    """Supervisor state file written on start and erased on stop."""
    sup = Supervisor(["p-claude"], runtime_dir=runtime_dir, popen=fake_popen)
    state_path = runtime_dir / "supervisor.json"
    assert not state_path.exists()
    sup.start_all()
    assert state_path.exists()

    # Start with no real PID liveness (don't escalate to SIGKILL by accident);
    # mark child dead first so stop_all just walks past it.
    pid = fake_popen.calls[0]["pid"]
    fake_popen.mark_dead(pid)

    sup.stop_all(grace_s=0.05)
    assert not state_path.exists(), "stop_all should clear the state file"


# ---------------------------------------------------------------------------
# Launchd plist generation
# ---------------------------------------------------------------------------


def test_launchd_plist_is_valid_xml(tmp_path: Path) -> None:
    """The plist round-trips through plistlib without raising."""
    target = tmp_path / "dev.quorus.reflexd.plist"
    written = launchd_mod.install_launchd(target, quorus_bin="/opt/bin/quorus")
    assert written.exists()
    parsed = plistlib.loads(written.read_bytes())
    # Spec sanity: known keys are present and typed correctly.
    assert parsed["Label"] == "dev.quorus.reflexd"
    assert isinstance(parsed["ProgramArguments"], list)
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is True


def test_launchd_plist_runs_correct_command(tmp_path: Path) -> None:
    """ProgramArguments should be exactly ``[<bin>, 'reflexd-manager', 'start']``."""
    raw = launchd_mod.render_plist(quorus_bin="/usr/local/bin/quorus")
    parsed = plistlib.loads(raw)
    assert parsed["ProgramArguments"] == [
        "/usr/local/bin/quorus", "reflexd-manager", "start",
    ]
    # And the std{out,err} paths land under ~/.quorus by default.
    assert parsed["StandardOutPath"].endswith("reflexd-manager.out.log")
    assert parsed["StandardErrorPath"].endswith("reflexd-manager.err.log")


# ---------------------------------------------------------------------------
# Smoke: a real ``subprocess.Popen`` integration path
# ---------------------------------------------------------------------------


def test_real_popen_uses_start_new_session(
    runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default Popen invocation should request a fresh session group.

    We can't easily assert kernel-level process-group state, but we *can*
    intercept the kwargs passed into the real Popen and verify the
    detach-on-Linux/macOS flag is set.
    """
    captured: dict = {}

    real_popen = subprocess.Popen

    class _RecordingPopen(real_popen):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            captured["kwargs"] = kwargs
            captured["args"] = args
            # Don't actually spawn — raise so start_all logs + skips the row.
            raise OSError("intercepted in test")

    monkeypatch.setattr(subprocess, "Popen", _RecordingPopen)
    sup = Supervisor(["smoke-claude"], runtime_dir=runtime_dir)
    # We expect an empty spawned dict because Popen raised.
    spawned = sup.start_all()
    assert spawned == {}
    # But the supervisor MUST have asked for start_new_session.
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["close_fds"] is True


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_state_persistence_survives_reload(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    """A second supervisor instance should pick up the persisted state."""
    sup1 = Supervisor(["r-claude"], runtime_dir=runtime_dir, popen=fake_popen)
    sup1.start_all()

    sup2 = Supervisor(["r-claude"], runtime_dir=runtime_dir, popen=fake_popen)
    health = sup2.health()
    assert health["r-claude"] == "alive"


def test_start_is_idempotent_for_already_running(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    """A second start_all should NOT re-spawn already-alive children."""
    sup = Supervisor(["i-claude"], runtime_dir=runtime_dir, popen=fake_popen)
    sup.start_all()
    spawn_count_before = len(fake_popen.calls)
    sup.start_all()  # second call
    assert len(fake_popen.calls) == spawn_count_before


def test_watchdog_tick_is_no_op_when_all_alive(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    sup = Supervisor(["q-claude"], runtime_dir=runtime_dir, popen=fake_popen)
    sup.start_all()
    spawn_count = len(fake_popen.calls)
    sup._watchdog_tick()
    sup._watchdog_tick()
    assert len(fake_popen.calls) == spawn_count


def test_restart_history_decays_outside_window(
    runtime_dir: Path, fake_popen: FakePopenFactory
) -> None:
    """Old restart entries past the 10-min window are dropped from the budget."""
    sup = Supervisor(["d-claude"], runtime_dir=runtime_dir, popen=fake_popen)
    sup.start_all()
    rec = sup._children["d-claude"]
    # Pre-load fake history that's already past the window.
    rec.restart_history = [time.time() - 999.0] * (RESTART_BUDGET + 2)
    fake_popen.mark_dead(rec.pid)
    sup._watchdog_tick()
    assert rec.crash_looping is False
    assert rec.restart_count == 1
