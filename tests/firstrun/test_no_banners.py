"""A first-run harness never puts a banner on the real desktop (2026-10-19: an
F5 run showed Arav "Quorus — general · alice: @alice-codex fix the bug in
calc.py"). The session env says QUORUS_NOTIFY=0, and stub notifiers first on
PATH catch anything that ignores it; PtySession.close fails on any banner."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("fcntl", reason="needs a POSIX pty")
from tests.firstrun.pty_session import (
    NOTIFIER_STUBS,
    assert_no_banners,
    banner_calls,
    child_env,
)

pytestmark = pytest.mark.firstrun

_BANNER = ("from quorus.notifications import notify; "
           "notify('Quorus — general', '@alice-codex fix the bug in calc.py', "
           "sender='alice', room='general')")


def _run(env: dict[str, str]) -> None:
    subprocess.run([sys.executable, "-c", _BANNER], env=env, cwd=env["HOME"],
                   check=True, timeout=30)


def test_the_session_env_turns_banners_off(tmp_path) -> None:
    env = child_env(tmp_path, "http://127.0.0.1:9")
    assert env["QUORUS_NOTIFY"] == "0"
    stubs = env["PATH"].split(":")[0]
    for name in NOTIFIER_STUBS:
        assert (tmp_path / ".notifier-stubs" / name).is_file()
    assert stubs == str(tmp_path / ".notifier-stubs")
    _run(env)
    assert banner_calls(tmp_path) == []
    assert_no_banners(tmp_path)


def test_a_banner_that_ignores_the_switch_hits_the_stub(tmp_path) -> None:
    """What 0.6.0 (no gate) does under stranger_nightly: the banner still
    never reaches the desktop, and close() would fail a gated wheel for it."""
    env = {**child_env(tmp_path, "http://127.0.0.1:9"), "QUORUS_NOTIFY": "1"}
    _run(env)
    calls = banner_calls(tmp_path)
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        assert len(calls) == 1 and "fix the bug in calc.py" in calls[0]
        with pytest.raises(AssertionError, match="desktop banner"):
            assert_no_banners(tmp_path)
