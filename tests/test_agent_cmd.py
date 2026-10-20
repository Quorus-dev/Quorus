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
        self.name = None
        self.model = None
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
                        lambda c, name, cred, legacy, model=None: calls["daemons"].append(
                            (name, cred, legacy)) or "label")
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda *a, **k: None)
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


def test_adding_an_agent_keeps_the_rooms_other_settings(env: dict, tmp_path: Path) -> None:
    """Bug bash: binding the room for a new agent dropped `listen`, `judge` and
    `share`, so an ambient room quietly went back to the rules."""
    repo = tmp_path / "app"
    repo.mkdir()
    path = tmp_path / ".quorus" / "room-bindings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"build": {
        "path": str(repo.resolve()), "mode": "autonomous", "listen": "ambient",
        "judge": ["j"], "judge_per_hour": 5, "share": True}}))
    agent_cmd.cmd_agent(_Args(repo=str(repo), mode=None, name="docs"), _Console())
    assert json.loads(path.read_text())["build"] == {
        "path": str(repo.resolve()), "mode": "autonomous", "listen": "ambient",
        "judge": ["j"], "judge_per_hour": 5, "share": True}


def test_existing_daemon_is_reused_never_duplicated(env: dict, monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd, "_running_daemon_for",
                        lambda *a, **k: "dev.quorus.dogfood.reflexd.arav-claude")
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
    assert env["joins"][-1] == ("build", "arav-codex", "jwt:parent-key")
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


def test_each_agent_daemon_gets_its_own_log(tmp_path: Path, monkeypatch) -> None:
    # Every agent used to log into one shared ~/.quorus/reflexd.log.
    import sys as _sys

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(agent_cmd, "_LA", tmp_path / "LA")
    monkeypatch.setattr(agent_cmd.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(_sys, "platform", "darwin")
    agent_cmd._start_daemon({"relay_url": "http://r"}, "a-claude", "k", True)
    xml = (tmp_path / "LA" / "dev.quorus.agent.a-claude.plist").read_text()
    assert "<key>REFLEXD_LOG_PATH</key>" in xml and "a-claude.log</string>" in xml
    assert "a-claude.crash.log" in xml  # launchd output kept out of the rotating log



def test_name_label_adds_a_second_agent_of_the_same_tool(env: dict, monkeypatch) -> None:
    # Users could run only one agent per tool: a second `agent add claude`
    # reused arav-claude.
    agent_cmd.cmd_agent(_Args(name="backend"), _Console())
    assert env["joins"][-1] == ("build", "arav-backend-claude", "s3")
    assert env["daemons"][-1][0] == "arav-backend-claude"
    with pytest.raises(SystemExit, match="--name"):
        agent_cmd.cmd_agent(_Args(name="Bad Name!"), _Console())


def test_labelled_agent_registers_under_its_own_suffix(env: dict, monkeypatch) -> None:
    env["cfg"]["api_key"] = "parent-key"
    import quorus_cli.cli as cli

    minted: list[str] = []
    monkeypatch.setattr(cli, "_register_agent_identity",
                        lambda url, key, suffix: minted.append(suffix) or "agent-key")
    monkeypatch.setattr(agent_cmd, "_bearer", lambda c, cred, legacy: f"jwt:{cred}")
    agent_cmd.cmd_agent(_Args(tool="opencode", name="local"), _Console())
    assert minted == ["local-opencode"]  # relay names it arav-local-opencode


def test_model_is_passed_to_the_daemon(tmp_path: Path, monkeypatch) -> None:
    import sys as _sys

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(agent_cmd, "_LA", tmp_path / "LA")
    monkeypatch.setattr(agent_cmd.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(_sys, "platform", "darwin")
    agent_cmd._start_daemon({"relay_url": "http://r"}, "a-opencode", "k", True,
                            "ollama/qwen3:4b")
    xml = (tmp_path / "LA" / "dev.quorus.agent.a-opencode.plist").read_text()
    assert "<key>REFLEXD_MODEL</key><string>ollama/qwen3:4b</string>" in xml
    assert f"<key>HOME</key><string>{Path.home()}</string>" in xml


def test_warns_before_adding_an_agent_that_cannot_wake(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert any("aistudio" in w for w in agent_cmd._readiness_warnings("gemini", None))
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / ".env").write_text("GEMINI_API_KEY=x\n")
    assert agent_cmd._readiness_warnings("gemini", "gemini-3.5-flash-lite") == []
    assert any("flash-lite" in w for w in agent_cmd._readiness_warnings("gemini", None))
    monkeypatch.setattr(agent_cmd, "_ollama_models", lambda: None)
    assert any("isn't running" in w
               for w in agent_cmd._readiness_warnings("opencode", "ollama/qwen3:4b"))
    monkeypatch.setattr(agent_cmd, "_ollama_models", lambda: ["llama3:latest"])
    assert any("ollama pull qwen3:4b" in w
               for w in agent_cmd._readiness_warnings("opencode", "ollama/qwen3:4b"))
    assert any("--model" in w for w in agent_cmd._readiness_warnings("opencode", None))


def test_cli_accepts_any_model_flags() -> None:
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", "import sys; from quorus_cli.cli import main; "
         "sys.argv=['quorus','agent','add','--help']; main()"],
        capture_output=True, text=True, timeout=60,
    )
    for flag in ("opencode", "--name", "--model"):
        assert flag in out.stdout, out.stderr[-300:]


@pytest.fixture
def invitee(env: dict, tmp_path: Path, monkeypatch) -> dict[str, Any]:
    """bob on alice's relay: his own member key, no secret."""
    env["cfg"].update(instance_name="bob", relay_secret="", api_key="quorus_pk_bob")
    monkeypatch.setattr(agent_cmd, "_claim_member_agent", lambda c, name: "agent-key")
    monkeypatch.setattr(agent_cmd, "_pin_owner", lambda c, name: None)
    monkeypatch.setattr(agent_cmd, "_bearer", lambda c, cred, legacy: f"jwt:{cred}")
    repo = tmp_path / "app"
    repo.mkdir()
    env["repo"] = repo
    return env


def _bound_mode(tmp_path: Path) -> str:
    entry = json.loads((tmp_path / ".quorus" / "room-bindings.json").read_text())["build"]
    return entry["mode"] if isinstance(entry, dict) else "default"  # a bare path: default


@pytest.mark.parametrize(("answer", "mode"), [(False, "default"), (True, "autonomous")])
def test_an_invitee_is_told_whose_relay_before_write_access(invitee, tmp_path, monkeypatch,
                                                           answer, mode) -> None:
    """codex, review of 0f9a90b7: the relay-owner note was only in the wizard. A
    direct `agent add --mode autonomous` on a teammate's relay shows it first and
    asks, and Enter keeps the tool's own permissions."""
    import rich.prompt

    monkeypatch.setattr(agent_cmd.sys.stdin, "isatty", lambda: True)
    asked = []
    monkeypatch.setattr(rich.prompt.Confirm, "ask",
                        classmethod(lambda cls, q, default=None: asked.append(default) or answer))
    console = _Console()
    agent_cmd.cmd_agent(_Args(tool="codex", repo=str(invitee["repo"]), mode="autonomous"),
                        console)
    note = next(i for i, ln in enumerate(console.lines) if agent_cmd.TEAMMATE_RELAY_NOTE in ln)
    joined = next(i for i, ln in enumerate(console.lines) if "joined #build" in ln)
    assert note < joined and asked == [False]
    assert _bound_mode(tmp_path) == mode


def test_without_a_terminal_the_explicit_flag_is_the_consent(invitee, tmp_path,
                                                             monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd.sys.stdin, "isatty", lambda: False)
    console = _Console()
    agent_cmd.cmd_agent(_Args(tool="codex", repo=str(invitee["repo"]), mode="autonomous"),
                        console)
    assert any(agent_cmd.TEAMMATE_RELAY_NOTE in ln for ln in console.lines)
    assert _bound_mode(tmp_path) == "autonomous"


def test_no_note_on_your_own_relay_or_without_write_access(env, invitee, tmp_path) -> None:
    console = _Console()
    agent_cmd.cmd_agent(_Args(tool="codex", repo=str(invitee["repo"]), mode="default"),
                        console)
    agent_cmd.cmd_agent(_Args(tool="claude", repo=str(invitee["repo"]), mode="autonomous",
                              disclosed=True), console)  # the wizard already asked
    assert not any(agent_cmd.TEAMMATE_RELAY_NOTE in ln for ln in console.lines)
