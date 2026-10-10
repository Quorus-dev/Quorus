"""`quorus agent add|list|remove` — the user-facing way to put agents in a room.

Before 2026-10-09 only scripts/dogfood.sh could give an agent a wake daemon;
`quorus add-agent` built a legacy polling workspace instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from quorus_cli import agent_cmd


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *a: Any, **_k: Any) -> None:
        self.lines.append(" ".join(str(x) for x in a))


class _Args:
    def __init__(self, **kw: Any) -> None:
        self.agent_action = "add"
        self.tool = "claude"
        self.room = "build"
        self.repo = None
        self.mode = "default"
        self.__dict__.update(kw)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(agent_cmd.Path, "home", staticmethod(lambda: tmp_path))
    cfg = {"instance_name": "arav", "relay_url": "http://r", "relay_secret": "s3",
           "api_key": ""}
    monkeypatch.setattr(agent_cmd, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(agent_cmd.shutil, "which", lambda b: f"/usr/bin/{b}")
    calls: dict[str, Any] = {"joins": [], "daemons": []}
    monkeypatch.setattr(agent_cmd, "_join",
                        lambda c, room, name, bearer: calls["joins"].append((room, name, bearer)))
    monkeypatch.setattr(agent_cmd, "_start_daemon",
                        lambda c, name, cred, legacy: calls["daemons"].append(
                            (name, cred, legacy)) or "label")
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda name: None)
    calls["cfg"] = cfg
    return calls


def test_add_joins_starts_daemon_and_binds_with_mode(env: dict, tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    agent_cmd.cmd_agent(_Args(repo=str(repo), mode="autonomous"), _Console())
    assert env["joins"] == [("build", "arav-claude", "s3")]
    assert env["daemons"] == [("arav-claude", "s3", True)]  # shared-secret relay
    bindings = json.loads((tmp_path / ".quorus" / "room-bindings.json").read_text())
    assert bindings["build"] == {"path": str(repo.resolve()), "mode": "autonomous"}


def test_existing_daemon_is_reused_never_duplicated(env: dict, monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd, "_running_daemon_for",
                        lambda name: "dev.quorus.dogfood.reflexd.arav-claude")
    console = _Console()
    agent_cmd.cmd_agent(_Args(), console)
    assert env["daemons"] == []  # two daemons would answer every message twice
    assert any("already running" in line for line in console.lines)


def test_account_relay_mints_and_caches_an_agent_key(env: dict, monkeypatch) -> None:
    env["cfg"]["api_key"] = "parent-key"
    minted: list[str] = []
    import quorus_cli.cli as cli

    monkeypatch.setattr(cli, "_register_agent_identity",
                        lambda url, key, suffix: minted.append(suffix) or "agent-key")
    monkeypatch.setattr(agent_cmd, "_bearer", lambda c, cred, legacy: f"jwt:{cred}")
    for _ in range(2):
        agent_cmd.cmd_agent(_Args(tool="codex"), _Console())
    assert minted == ["codex"]  # second run reuses the cached key
    assert env["joins"][-1] == ("build", "arav-codex", "jwt:agent-key")
    assert env["daemons"][-1] == ("arav-codex", "agent-key", False)


def test_refuses_missing_cli_and_unnamed_user(env: dict, monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd.shutil, "which", lambda b: None)
    with pytest.raises(SystemExit, match="isn't installed"):
        agent_cmd.cmd_agent(_Args(), _Console())
    monkeypatch.setattr(agent_cmd.shutil, "which", lambda b: "/x")
    env["cfg"]["instance_name"] = "default"
    with pytest.raises(SystemExit, match="set your name"):
        agent_cmd.cmd_agent(_Args(), _Console())


def test_plist_escapes_and_keeps_alive() -> None:
    xml = agent_cmd._plist("dev.quorus.agent.a-claude", ["/py", "x&y"],
                           {"API_KEY": "k<1>"}, Path("/tmp/l.log"))
    assert "x&amp;y" in xml and "k&lt;1&gt;" in xml
    assert "<key>KeepAlive</key><true/>" in xml


def test_cli_exposes_agent_commands() -> None:
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", "import sys; from quorus_cli.cli import main; "
         "sys.argv=['quorus','agent','add','--help']; main()"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr[-400:]
    for flag in ("--room", "--repo", "--mode", "claude", "codex"):
        assert flag in out.stdout


def test_version_flag_prints_version() -> None:
    # `quorus --version` errored "unrecognized arguments" on a clean install.
    import subprocess
    import sys

    from quorus import __version__

    out = subprocess.run(
        [sys.executable, "-c", "import sys; from quorus_cli.cli import main; "
         "sys.argv=['quorus','--version']; main()"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0 and __version__ in out.stdout, out.stderr[-300:]
