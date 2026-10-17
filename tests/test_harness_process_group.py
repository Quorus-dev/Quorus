"""A harness that leaves a child running must never hang its agent (2026-10-17).

The Gemini CLI relaunches itself as a child node process. On a timeout the
daemon killed only the parent; the child kept working, held the output pipes,
and the wake waited forever (Python 3.12+'s ``proc.wait()`` waits for every
pipe to close). Fixed by giving each harness its own process group, killing
the group, and never waiting without a limit.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _harness(tmp_path: Path, body: str) -> Path:
    exe = tmp_path / "fakeharness"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(0o755)
    return exe


def _alive(pidfile: Path) -> bool:
    try:
        os.kill(int(pidfile.read_text()), 0)
    except (OSError, ValueError):
        return False
    return True


def test_timeout_kills_the_relaunched_child_and_returns(tmp_path: Path, monkeypatch) -> None:
    pid = tmp_path / "child.pid"
    # Like the Gemini CLI: the work happens in a child that outlives the parent.
    exe = _harness(tmp_path, f"(echo $$ > /dev/null; sh -c 'echo $$ > {pid}; sleep 60') &\n"
                             "sleep 60\n")
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    adapter = reflexd.HeadlessAdapter(timeout_s=1)
    start = time.time()
    out = asyncio.run(adapter._run_subprocess([exe.name], parser=lambda s: s, timeout_s=1))
    assert out == "[reflexd] harness timed out"
    assert time.time() - start < 15
    time.sleep(0.3)
    assert not _alive(pid), "the child kept running after the timeout"


def test_a_child_holding_the_pipes_after_exit_is_killed(tmp_path: Path, monkeypatch) -> None:
    pid = tmp_path / "bg.pid"
    # The harness answers and exits, but a background job it started keeps stdout.
    exe = _harness(tmp_path, f"sh -c 'echo $$ > {pid}; sleep 60' &\necho done\nexit 0\n")
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(reflexd, "LINGER_S", 0.5)
    adapter = reflexd.HeadlessAdapter(timeout_s=30)
    adapter.on_step = lambda step: None  # the streaming path, as in a real wake
    start = time.time()
    out = asyncio.run(adapter._run_subprocess([exe.name], parser=lambda s: s.strip(),
                                              timeout_s=30))
    assert out == "done"
    assert time.time() - start < 10
    time.sleep(0.3)
    assert not _alive(pid)
