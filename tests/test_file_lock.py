"""Every module the CLI and relay import at startup must import without fcntl.

PR #20's Windows cold-install crashed every `quorus` command: cli.main imports
handoff_cmd -> handoff_release, which imported fcntl at module level, and the relay
imports room_track -> task_record the same way.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quorus.runtime import file_lock

ROOT = Path(__file__).resolve().parents[1]
STARTUP = ("quorus.runtime.handoff_release", "quorus.runtime.task_record",
           "quorus.routes.room_track", "quorus_cli.handoff_cmd", "quorus_cli.track_cmd")


def test_startup_modules_import_where_fcntl_does_not_exist():
    code = ("import sys; sys.modules['fcntl'] = None\n"
            + "".join(f"import {m}\n" for m in STARTUP) + "print('ok')")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        str(ROOT / p) for p in (".", "packages/cli", "packages/mcp", "packages/sdk",
                                "packages/tui"))}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, cwd=ROOT, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr[-600:]


def test_without_any_lock_primitive_locking_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(file_lock, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    fd = os.open(tmp_path / "x.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with pytest.raises(OSError):
            file_lock.lock(fd, blocking=False)
    finally:
        os.close(fd)


def test_a_held_lock_refuses_a_second_non_blocking_holder(tmp_path):
    path = tmp_path / "x.lock"
    first = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    second = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        file_lock.lock(first, blocking=False)
        with pytest.raises(OSError):
            file_lock.lock(second, blocking=False)
    finally:
        os.close(second)
        os.close(first)
