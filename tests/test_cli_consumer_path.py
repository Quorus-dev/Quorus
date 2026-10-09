"""Regular-consumer path regressions found by the 2026-10-09 review.

* `quorus relay` read the secret from the config POINTER file (always empty
  with profiles), ran `uv run` in the source checkout (broken on pip/pipx
  installs), bound 0.0.0.0 and wrote state into the current directory.
* `quorus init` auto-started reflexd-manager, whose <you>-claude/... daemons
  duplicated the ones `quorus agent add` starts (every message answered twice).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def test_relay_uses_profile_secret_installed_entrypoint_and_safe_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import quorus_cli.cli as cli

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("RELAY_SECRET", raising=False)
    monkeypatch.delenv("MESSAGES_FILE", raising=False)
    monkeypatch.setattr(cli, "load_config", lambda: {"relay_secret": "from-profile"})
    seen: dict[str, Any] = {}

    def fake_run(argv, env=None, check=False):
        seen["argv"], seen["env"] = argv, env
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._cmd_relay(SimpleNamespace(port=9123, host=None))
    assert seen["argv"] == [sys.executable, "-m", "quorus.relay_cli"]  # no uv, no checkout
    env = seen["env"]
    assert env["RELAY_SECRET"] == "from-profile"
    assert env["HOST"] == "127.0.0.1" and env["PORT"] == "9123"
    assert env["MESSAGES_FILE"] == str(tmp_path / "relay-state.json")
    assert env["ALLOW_LEGACY_AUTH"] == "1"


def test_relay_without_secret_explains_the_order(monkeypatch, capsys) -> None:
    import quorus_cli.cli as cli

    monkeypatch.delenv("RELAY_SECRET", raising=False)
    monkeypatch.setattr(cli, "load_config", lambda: {})
    with pytest.raises(SystemExit):
        cli._cmd_relay(SimpleNamespace(port=1, host=None))
    assert "quorus init" in capsys.readouterr().out


def test_init_does_not_start_agents_by_default(tmp_path: Path) -> None:
    out = subprocess.run(
        [sys.executable, "-c", "import sys; from quorus_cli.cli import main; "
         "sys.argv=['quorus','init','zed','--secret','s','--relay-url','http://127.0.0.1:9',"
         "'--no-smoke']; main()"],
        env={"HOME": str(tmp_path), "QUORUS_CONFIG_DIR": str(tmp_path / ".q"),
             "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=120,
    )
    text = out.stdout + out.stderr
    assert "reflexd-manager: running" not in text
    assert "quorus agent add claude" in text  # the next step points at the real flow


def test_agent_add_sees_a_plain_daemon_process(monkeypatch) -> None:
    from quorus_cli import agent_cmd

    monkeypatch.setattr(agent_cmd, "_launchd_labels", lambda: [])
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="4242\n", stderr="")

    monkeypatch.setattr(agent_cmd.subprocess, "run", fake_run)
    assert agent_cmd._running_daemon_for("zed-claude") == "pid 4242"
    assert "--participant zed-claude( |$)" in calls[-1][-1]
    json.dumps(calls)  # argv stays serialisable
