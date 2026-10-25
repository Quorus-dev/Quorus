"""`quorus agent add` says "is running" only for a daemon that stayed up (take-3
bug 1, docs/plans/take3-fixes.md): launchctl's exit code was ignored, and a
daemon that crashed on start was reported running."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from quorus_cli import agent_cmd, agent_start

from tests.test_agent_cmd import _Console, _no_spawn

SECRET = "quorus_pk_" + "x" * 24
NAME = "arav-claude"
LABEL = agent_cmd.LABEL_PREFIX + NAME
_POPEN = subprocess.Popen  # before the fixture swaps it out


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(agent_cmd, "_LA", tmp_path / "LA")
    monkeypatch.setattr(agent_start, "WAIT_S", 0.6)
    monkeypatch.setattr(agent_start, "SETTLE_S", 0.2)
    monkeypatch.setattr(agent_cmd.subprocess, "Popen", _no_spawn)
    return tmp_path


class _Launchctl:
    """Records launchctl calls; *bootstrap* is its exit code and stderr."""

    def __init__(self, bootstrap: tuple[int, str] = (0, ""), crash: str = "") -> None:
        self.calls: list[list[str]] = []
        self.bootstrap, self.crash = bootstrap, crash

    def __call__(self, argv: list[str], **_k: Any) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        if argv[:2] == ["launchctl", "bootstrap"]:
            if self.crash:  # the job ran and died, writing its traceback
                log = agent_cmd._agents_dir() / f"{NAME}.crash.log"
                with log.open("a") as fh:
                    fh.write(self.crash)
            return subprocess.CompletedProcess(argv, self.bootstrap[0], "", self.bootstrap[1])
        return subprocess.CompletedProcess(argv, 0, "", "")


def _launchd(monkeypatch, fake: _Launchctl, pid: int | None) -> None:
    monkeypatch.setattr(agent_cmd, "_launchd_ok", lambda: True)
    monkeypatch.setattr(agent_cmd.subprocess, "run", fake)
    monkeypatch.setattr(agent_cmd, "_launchd_jobs", lambda: {LABEL: pid})


def _prep() -> dict[str, Any]:
    return {"cfg": {"relay_url": "http://r", "relay_secret": "s3"}, "me": "arav",
            "name": NAME, "room": "build", "repo": None, "model": None, "state_dir": None,
            "existing": None, "cred": SECRET, "legacy": True, "bearer": "b"}


def test_a_failed_bootstrap_is_not_running(home, monkeypatch) -> None:
    fake = _Launchctl(bootstrap=(5, "Bootstrap failed: 5: Input/output error\nmore"))
    _launchd(monkeypatch, fake, pid=None)
    console = _Console()
    with pytest.raises(SystemExit, match="didn't start: launchctl said Bootstrap failed: 5"):
        agent_cmd._start(_prep(), console)
    assert not any("is running" in line for line in console.lines)
    assert not (home / "LA" / f"{LABEL}.plist").exists()  # no failing job at every login


def test_a_daemon_that_dies_at_once_shows_its_log_without_secrets(home, monkeypatch) -> None:
    agent_cmd._agents_dir().mkdir(parents=True, exist_ok=True)
    old = agent_cmd._agents_dir() / f"{NAME}.crash.log"
    old.write_text("an old run's line\n")
    fake = _Launchctl(crash=f"Traceback\nRuntimeError: relay refused {SECRET}\n")
    _launchd(monkeypatch, fake, pid=None)
    console = _Console()
    with pytest.raises(SystemExit) as exc:
        agent_cmd._start(_prep(), console)
    text = str(exc.value.code)
    assert "didn't stay up; its last log lines:" in text and "RuntimeError" in text
    assert SECRET not in text and "[redacted]" in text
    assert "old run" not in text  # only this start's output
    assert ["launchctl", "bootout", f"gui/{agent_cmd._uid()}/{LABEL}"] == fake.calls[-1]
    assert not (home / "LA" / f"{LABEL}.plist").exists()  # no KeepAlive crash loop
    assert not any("is running" in line for line in console.lines)


def test_a_detached_daemon_that_exits_is_not_running(home, monkeypatch) -> None:
    code = f"import sys; print('boom {SECRET}', file=sys.stderr); sys.exit(1)"
    monkeypatch.setattr(agent_cmd, "_launchd_ok", lambda: False)
    monkeypatch.setattr(agent_cmd.subprocess, "Popen",
                        lambda argv, **kw: _POPEN([sys.executable, "-c", code], **kw))
    with pytest.raises(SystemExit, match="didn't stay up") as exc:
        agent_cmd._start_daemon({"relay_url": "http://r"}, NAME, SECRET, True)
    assert "boom [redacted]" in str(exc.value.code)
    assert not (agent_cmd._agents_dir() / f"{NAME}.pid").exists()


def test_a_healthy_daemon_is_running(home, monkeypatch) -> None:
    fake = _Launchctl()
    _launchd(monkeypatch, fake, pid=4242)
    assert agent_cmd._start_daemon({"relay_url": "http://r"}, NAME, SECRET, True) == LABEL
    assert (home / "LA" / f"{LABEL}.plist").exists()
    # A crashed job of ours is still loaded: it's booted out before the bootstrap.
    assert [c[1] for c in fake.calls] == ["bootout", "bootstrap"]


def test_a_crash_loop_never_settles() -> None:
    pids = iter(range(100, 10_000))
    assert not agent_start.stays_up(lambda: next(pids), wait_s=0.3, settle_s=0.1,
                                    poll_s=0.02)
    assert agent_start.stays_up(lambda: 7, wait_s=0.3, settle_s=0.1, poll_s=0.02)


def test_a_dead_launchd_job_of_ours_is_not_already_running(home, monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd.subprocess, "run",
                        lambda a, **k: subprocess.CompletedProcess(a, 1, "", ""))  # no pgrep hit
    monkeypatch.setattr(agent_cmd, "_launchd_jobs", lambda: {LABEL: None})
    assert agent_cmd._running_daemon_for(NAME) is None
    monkeypatch.setattr(agent_cmd, "_launchd_jobs", lambda: {LABEL: 4242})
    assert agent_cmd._running_daemon_for(NAME) == LABEL
    # Another job's label still counts while it's down: KeepAlive may restart it.
    dogfood = f"dev.quorus.dogfood.reflexd.{NAME}"
    monkeypatch.setattr(agent_cmd, "_launchd_jobs", lambda: {dogfood: None})
    assert agent_cmd._running_daemon_for(NAME) == dogfood


def test_launchctl_list_pids_are_read(monkeypatch) -> None:
    out = f"PID\tStatus\tLabel\n-\t78\t{LABEL}\n812\t0\tdev.quorus.agent.arav-codex\n"
    monkeypatch.setattr(agent_cmd, "_launchd_ok", lambda: True)
    monkeypatch.setattr(agent_cmd.subprocess, "run",
                        lambda a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    assert agent_cmd._launchd_jobs() == {LABEL: None, "dev.quorus.agent.arav-codex": 812}


def test_list_shows_a_dead_job_as_stopped(home, monkeypatch, capsys) -> None:
    monkeypatch.setattr(agent_cmd, "load_config", lambda: {"instance_name": "arav"})
    monkeypatch.setattr(agent_cmd, "_launchd_jobs", lambda: {LABEL: None})
    agent_cmd._list(_Console(), as_json=True)
    assert '"running": false' in capsys.readouterr().out
