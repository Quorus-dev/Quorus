"""QUORUS_NO_LAUNCHD=1 keeps `quorus agent` away from launchctl.

launchd isn't scoped to HOME, so a test (or any sandbox) with a tmp HOME would
register agents in the real login session (loop C, first-run pty tests)."""

from __future__ import annotations

import sys

from quorus_cli import agent_cmd


def test_the_flag_refuses_launchd(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("QUORUS_NO_LAUNCHD", raising=False)
    assert agent_cmd._launchd_ok()
    monkeypatch.setenv("QUORUS_NO_LAUNCHD", "1")
    assert not agent_cmd._launchd_ok()


def test_no_launchctl_runs_under_the_flag(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("QUORUS_NO_LAUNCHD", "1")
    calls = []
    monkeypatch.setattr(agent_cmd.subprocess, "run", lambda *a, **k: calls.append(a))
    assert agent_cmd._launchd_labels() == []
    assert calls == []


def test_linux_never_had_launchd(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("QUORUS_NO_LAUNCHD", raising=False)
    assert not agent_cmd._launchd_ok()
