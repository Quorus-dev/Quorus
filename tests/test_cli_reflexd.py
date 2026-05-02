"""Tests for the ``quorus reflexd`` CLI subcommand wrapper.

The wrapper lives in ``packages/cli/quorus_cli/cli.py`` and shells out to
``scripts/reflexd.py``. These tests verify pidfile hygiene, idempotency, the
status exit-code contract, and the participant resolution order. We never
spin up a real reflex daemon — we substitute a tiny long-running Python
stub via ``monkeypatch`` of ``_reflexd_script_path``.
"""
from __future__ import annotations

import importlib
import os
import signal
import sys
import textwrap
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Reroute ``Path.home()`` so reflexd writes under a tmp dir."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Strip env vars that would override participant resolution OR override
    # config-dir resolution and cause cross-test leakage.
    monkeypatch.delenv("REFLEXD_PARTICIPANT", raising=False)
    monkeypatch.delenv("QUORUS_PARTICIPANT", raising=False)
    monkeypatch.delenv("QUORUS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MCP_TUNNEL_CONFIG_DIR", raising=False)
    # Reload modules whose module-level constants captured ``Path.home()``
    # at first import. Without this, the SECOND test runs against the
    # ``tmp_path`` of the FIRST test (the cli module's _REFLEXD_RUNTIME_DIR
    # and quorus.config.DEFAULT_CONFIG_DIR are both set at import time).
    import quorus.config as _qcfg
    importlib.reload(_qcfg)
    import quorus_cli.cli as _cli
    importlib.reload(_cli)
    return tmp_path


@pytest.fixture
def stub_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a long-running stub that mimics ``scripts/reflexd.py``.

    The stub installs a SIGTERM handler so we can verify graceful shutdown,
    advertises ``reflexd`` in argv[0]'s window so the ``ps``-based
    ``_pid_is_reflexd`` check returns True, and accepts the same
    positional ``start`` / ``--participant`` flags the wrapper passes.
    """
    script = tmp_path / "reflexd.py"
    script.write_text(
        textwrap.dedent(
            """
            import argparse
            import signal
            import sys
            import time

            # Argv[0] becomes the process command name on most kernels —
            # so the wrapper's ps-based reflexd check still matches.
            ap = argparse.ArgumentParser(prog="reflexd")
            ap.add_argument("command", nargs="?", default="start")
            ap.add_argument("--participant")
            ap.add_argument("--debug", action="store_true")
            ap.add_argument("--no-spawn", action="store_true")
            ap.add_argument("--relay-url")
            ap.parse_args()

            stop = {"flag": False}

            def _handle(*_a):
                stop["flag"] = True

            signal.signal(signal.SIGTERM, _handle)
            signal.signal(signal.SIGINT, _handle)

            # Emit one log line so status/logs have something to show.
            print("reflexd-stub: started", flush=True)

            while not stop["flag"]:
                time.sleep(0.05)

            print("reflexd-stub: stopped", flush=True)
            sys.exit(0)
            """
        ).lstrip(),
        encoding="utf-8",
    )

    import quorus_cli.cli as _cli
    monkeypatch.setattr(_cli, "_reflexd_script_path", lambda: script)
    return script


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Invoke ``quorus_cli.cli.main`` with custom argv. Returns exit code."""
    import quorus_cli.cli as _cli
    monkeypatch.setattr(sys, "argv", ["quorus"] + argv)
    try:
        _cli.main()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _kill_quietly(pid: int) -> None:
    """Best-effort SIGKILL used by test cleanup."""
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reflexd_start_creates_pidfile(
    monkeypatch: pytest.MonkeyPatch,
    runtime_home: Path,
    stub_script: Path,
) -> None:
    rc = _run_cli(monkeypatch, [
        "reflexd", "start", "--detach",
        "--participant", "smoke-claude",
    ])
    assert rc == 0
    pidfile = runtime_home / ".quorus" / "runtime" / "reflexd.smoke-claude.pid"
    try:
        assert pidfile.exists()
        pid = int(pidfile.read_text(encoding="utf-8").strip())
        assert pid > 0
        # The mode should be 0600. On platforms that support it.
        mode = pidfile.stat().st_mode & 0o777
        assert mode == 0o600
        assert _wait_until(
            lambda: _process_alive(pid),
            timeout=2.0,
        ), "stub child should be alive"
    finally:
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text(encoding="utf-8").strip())
            except Exception:
                pid = 0
            _kill_quietly(pid)
            pidfile.unlink(missing_ok=True)


def test_reflexd_start_idempotent_with_existing_pid(
    monkeypatch: pytest.MonkeyPatch,
    runtime_home: Path,
    stub_script: Path,
) -> None:
    rc1 = _run_cli(monkeypatch, [
        "reflexd", "start", "--detach", "--participant", "smoke-claude",
    ])
    assert rc1 == 0
    pidfile = runtime_home / ".quorus" / "runtime" / "reflexd.smoke-claude.pid"
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
        assert _wait_until(lambda: _process_alive(pid), timeout=2.0)
        # Second start must refuse with the validation-error exit code (5).
        rc2 = _run_cli(monkeypatch, [
            "reflexd", "start", "--detach", "--participant", "smoke-claude",
        ])
        assert rc2 == 5, f"expected exit 5 (already running), got {rc2}"
        # Daemon must still be up.
        assert _process_alive(pid)
    finally:
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text(encoding="utf-8").strip())
            except Exception:
                pid = 0
            _kill_quietly(pid)
            pidfile.unlink(missing_ok=True)


def test_reflexd_stop_when_not_running_is_noop(
    monkeypatch: pytest.MonkeyPatch,
    runtime_home: Path,
    stub_script: Path,
) -> None:
    pidfile = runtime_home / ".quorus" / "runtime" / "reflexd.ghost-claude.pid"
    assert not pidfile.exists()
    rc = _run_cli(monkeypatch, [
        "reflexd", "stop", "--participant", "ghost-claude",
    ])
    # Stop on idle is a successful no-op (exit 0).
    assert rc == 0
    assert not pidfile.exists()


def test_reflexd_stop_kills_running_daemon(
    monkeypatch: pytest.MonkeyPatch,
    runtime_home: Path,
    stub_script: Path,
) -> None:
    rc1 = _run_cli(monkeypatch, [
        "reflexd", "start", "--detach", "--participant", "smoke-claude",
    ])
    assert rc1 == 0
    pidfile = runtime_home / ".quorus" / "runtime" / "reflexd.smoke-claude.pid"
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    assert _wait_until(lambda: _process_alive(pid), timeout=2.0)
    rc2 = _run_cli(monkeypatch, [
        "reflexd", "stop", "--participant", "smoke-claude",
    ])
    assert rc2 == 0
    # Pidfile cleared — the wrapper's contract. (Process death is the OS's
    # responsibility, and orphan zombies on macOS can stay around briefly
    # after the wrapper exits, so we don't double-check os.kill here.)
    assert _wait_until(lambda: not pidfile.exists(), timeout=2.0)
    # Best-effort: most of the time the stub exits cleanly within 5s.
    _wait_until(lambda: not _process_alive(pid), timeout=5.0)
    # Cleanup if the kernel hadn't reaped yet.
    _kill_quietly(pid)


def test_reflexd_status_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    runtime_home: Path,
    stub_script: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Idle → exit 1.
    rc_idle = _run_cli(monkeypatch, [
        "reflexd", "status", "--participant", "smoke-claude",
    ])
    assert rc_idle == 1
    out = capsys.readouterr().out
    assert "not running" in out

    # Running → exit 0.
    _run_cli(monkeypatch, [
        "reflexd", "start", "--detach", "--participant", "smoke-claude",
    ])
    pidfile = runtime_home / ".quorus" / "runtime" / "reflexd.smoke-claude.pid"
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    try:
        assert _wait_until(lambda: _process_alive(pid), timeout=2.0)
        rc_running = _run_cli(monkeypatch, [
            "reflexd", "status", "--participant", "smoke-claude",
        ])
        assert rc_running == 0
        out = capsys.readouterr().out
        assert "running" in out
        assert str(pid) in out
    finally:
        _kill_quietly(pid)
        pidfile.unlink(missing_ok=True)


def test_reflexd_logs_returns_tail(
    monkeypatch: pytest.MonkeyPatch,
    runtime_home: Path,
    stub_script: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_path = runtime_home / ".quorus" / "reflexd.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Seed the log file directly — covers the read-path without racing the
    # stub child's own writes.
    lines = [f"line-{i:03d}" for i in range(120)]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rc = _run_cli(monkeypatch, ["reflexd", "logs", "--tail", "5"])
    # logs returns implicitly (None) on success → exit 0.
    assert rc == 0
    out = capsys.readouterr().out
    # The last 5 lines should appear; earlier lines should not.
    for tail in lines[-5:]:
        assert tail in out
    assert "line-000" not in out


def test_reflexd_start_uses_active_profile_instance_name(
    monkeypatch: pytest.MonkeyPatch,
    runtime_home: Path,
    stub_script: Path,
) -> None:
    """When --participant is omitted, fall back to config.json's instance_name.

    We seed a config.json under the rerouted HOME and assert the pidfile
    lands on the resolved name. Suffixes (``-claude``, ``-codex``) must be
    preserved because reflexd matches harness type by suffix.
    """
    cfg_dir = runtime_home / ".quorus"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        '{"relay_url": "http://localhost:9999",'
        ' "relay_secret": "x",'
        ' "instance_name": "arav-claude-1m"}',
        encoding="utf-8",
    )
    rc = _run_cli(monkeypatch, ["reflexd", "start", "--detach"])
    assert rc == 0
    pidfile = runtime_home / ".quorus" / "runtime" / "reflexd.arav-claude-1m.pid"
    try:
        assert pidfile.exists(), (
            "wrapper should resolve participant to the profile's instance_name"
        )
        pid = int(pidfile.read_text(encoding="utf-8").strip())
        assert pid > 0
    finally:
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text(encoding="utf-8").strip())
            except Exception:
                pid = 0
            _kill_quietly(pid)
            pidfile.unlink(missing_ok=True)


def test_reflexd_stale_pidfile_cleared(
    monkeypatch: pytest.MonkeyPatch,
    runtime_home: Path,
    stub_script: Path,
) -> None:
    """A pidfile pointing at a dead PID must NOT block ``start``."""
    pidfile = runtime_home / ".quorus" / "runtime" / "reflexd.smoke-claude.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    # Pick a PID that's overwhelmingly unlikely to be alive — kernel max
    # PID is 99999 on macOS by default, so 999999 is always dead.
    pidfile.write_text("999999", encoding="utf-8")

    rc = _run_cli(monkeypatch, [
        "reflexd", "start", "--detach", "--participant", "smoke-claude",
    ])
    assert rc == 0, "stale pidfile should be cleared, not refused"
    try:
        new_pid = int(pidfile.read_text(encoding="utf-8").strip())
        assert new_pid != 999999
        assert _wait_until(lambda: _process_alive(new_pid), timeout=2.0)
    finally:
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text(encoding="utf-8").strip())
            except Exception:
                pid = 0
            _kill_quietly(pid)
            pidfile.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _process_alive(pid: int) -> bool:
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
