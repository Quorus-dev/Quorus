"""Banners for your @-mentions while the hub is closed (`quorus notify on`).

The hub raised a banner only while it ran, so a teammate's agent that
@-mentioned you with the hub closed went unseen (live run, 2026-11-04)."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from quorus_cli import agent_cmd, notify_cmd
from quorus_tui import hub
from quorus_tui import notify_listener as nl


def test_mentions_and_dms_from_others_only() -> None:
    m = lambda **kw: {"from_name": "bob-claude", "content": "", **kw}  # noqa: E731
    assert nl.should_notify(m(content="@arav can you look?"), "arav")
    assert nl.should_notify(m(content="hi", is_dm=True), "arav")
    assert nl.should_notify(m(content="@ARAV ping"), "arav")
    assert not nl.should_notify(m(content="no mention here"), "arav")
    assert not nl.should_notify(m(from_name="arav", content="@arav note to self"), "arav")
    # Your own agents alert you themselves (agent_events): no second banner.
    assert not nl.should_notify(m(from_name="arav-codex", content="@arav done"), "arav")
    # A profile named after an agent posts as the human: @arav still counts.
    assert nl.should_notify(m(content="@arav review?"), "arav-codex", "arav")


class _Resp:
    status_code = 200

    def __init__(self, lines, stop):
        self.lines, self.stop = lines, stop

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def iter_lines(self):
        yield from self.lines
        self.stop.set()


def test_the_listener_banners_a_mention(monkeypatch) -> None:
    stop = threading.Event()
    events = [
        {"from_name": "bob-claude", "room": "build", "content": "@arav please review"},
        {"from_name": "arav-codex", "room": "build", "content": "@arav ✅ done"},
        {"from_name": "bob", "room": "build", "content": "unrelated"},
    ]
    lines = []
    for e in events:
        lines += ["event: message", f"data: {json.dumps(e)}", ""]
    monkeypatch.setattr(hub, "_get_auth_token", lambda relay, raw: "jwt")
    monkeypatch.setattr(hub, "_mint_sse_token", lambda relay, auth, me: "tok")
    monkeypatch.setattr(nl.httpx, "stream", lambda *a, **k: _Resp(lines, stop))
    sent: list[tuple[str, str, str]] = []
    import quorus.notifications as notifications
    monkeypatch.setattr(notifications, "notify",
                        lambda title, body, **kw: sent.append((title, body, kw["sender"])))
    nl.run({"relay_url": "http://r", "instance_name": "arav", "api_key": "k"}, stop)
    assert sent == [("Quorus — build", "@arav please review", "bob-claude")]


@pytest.fixture
def launchd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("QUORUS_NO_LAUNCHD", raising=False)
    monkeypatch.setattr(agent_cmd, "_LA", tmp_path / "LA")
    monkeypatch.setattr(agent_cmd, "_launchd_ok", lambda: True)
    monkeypatch.setattr(agent_cmd, "_launchd_jobs", lambda: {})
    import quorus.config as qc
    monkeypatch.setattr(qc, "load_config", lambda *a, **k: {"instance_name": "arav"})
    calls: list[list[str]] = []

    def fake_run(argv, **_k):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(notify_cmd.subprocess, "run", fake_run)
    return tmp_path, calls


class _Console:
    def __init__(self) -> None:
        self.out: list[str] = []

    def print(self, *a, **_k) -> None:
        self.out.append(" ".join(str(x) for x in a))


def test_on_installs_the_listener_job_and_off_removes_it(launchd) -> None:
    tmp, calls = launchd
    console = _Console()
    notify_cmd.cmd_notify(SimpleNamespace(notify_action="on"), console)
    plist = tmp / "LA" / "dev.quorus.notify.arav.plist"
    text = plist.read_text()
    assert "quorus_tui.notify_listener" in text and str(tmp / "cfg") in text
    assert any(c[:2] == ["launchctl", "bootstrap"] for c in calls)
    assert "even with the hub closed" in console.out[0]
    notify_cmd.cmd_notify(SimpleNamespace(notify_action="off"), console)
    assert not plist.exists()
    assert calls[-1][:2] == ["launchctl", "bootout"]
