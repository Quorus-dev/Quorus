"""CLI workspace subcommands."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from quorus.profiles import ProfileManager


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _run_cli(argv: list[str]) -> int:
    """Invoke quorus_cli.cli.main() with a fake argv. Returns exit code."""
    from quorus_cli import cli as cli_mod
    with patch.object(cli_mod.sys, "argv", ["quorus"] + argv):
        try:
            cli_mod.main()
        except SystemExit as e:
            return int(e.code or 0)
    return 0


def test_workspaces_list_empty(tmp_config_dir: Path, capsys) -> None:
    _run_cli(["workspaces"])
    out = capsys.readouterr().out
    assert "no workspaces" in out.lower() or "no profiles" in out.lower()


def test_workspaces_list_shows_current(tmp_config_dir: Path, capsys) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u1", "instance_name": "aarya"})
    pm.save("personal", {"relay_url": "u2", "instance_name": "aarya"})
    pm.set_current("work")

    _run_cli(["workspaces"])
    out = capsys.readouterr().out
    assert "work" in out
    assert "personal" in out
    # Current marker — implementation uses "*" or "(current)"
    assert "*" in out or "current" in out.lower()


def test_workspaces_use_switches_current(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u1"})
    pm.save("personal", {"relay_url": "u2"})
    pm.set_current("work")

    _run_cli(["workspaces", "use", "personal"])

    assert ProfileManager(config_dir=tmp_config_dir).current() == "personal"


def test_workspaces_use_unknown_errors(tmp_config_dir: Path, capsys) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})

    rc = _run_cli(["workspaces", "use", "ghost"])
    assert rc != 0
    captured = capsys.readouterr()
    combined = (captured.err + captured.out).lower()
    assert "not found" in combined or "no such" in combined


def test_workspaces_rm_deletes(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})
    pm.save("personal", {"relay_url": "u"})
    pm.set_current("work")

    _run_cli(["workspaces", "rm", "personal", "--yes"])

    assert ProfileManager(config_dir=tmp_config_dir).list() == ["work"]


def test_workspaces_rm_refuses_last(tmp_config_dir: Path, capsys) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})
    pm.set_current("work")

    rc = _run_cli(["workspaces", "rm", "work", "--yes"])
    assert rc != 0
    assert ProfileManager(config_dir=tmp_config_dir).list() == ["work"]


def test_whoami_shows_current(tmp_config_dir: Path, capsys) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {
        "relay_url": "https://r.test",
        "instance_name": "aarya",
        "workspace_label": "Work",
    })
    pm.set_current("work")

    _run_cli(["whoami"])
    out = capsys.readouterr().out
    assert "aarya" in out
    assert "https://r.test" in out


def test_whoami_reports_none_when_unset(tmp_config_dir: Path, capsys) -> None:
    rc = _run_cli(["whoami"])
    out = capsys.readouterr().out
    assert "no current workspace" in out.lower() or rc != 0


def test_workspace_flag_overrides_current(tmp_config_dir: Path, monkeypatch) -> None:
    """--workspace flag selects the profile for one command."""
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://work.test"})
    pm.save("personal", {"relay_url": "https://personal.test"})
    pm.set_current("work")

    # whoami with -w personal should report personal even though current is work.
    import contextlib
    import io

    from quorus_cli import cli as cli_mod
    buf = io.StringIO()
    with patch.object(cli_mod.sys, "argv",
                      ["quorus", "-w", "personal", "whoami"]):
        with contextlib.redirect_stdout(buf):
            try:
                cli_mod.main()
            except SystemExit:
                pass
    assert "https://personal.test" in buf.getvalue()


def test_mcp_server_honors_quorus_profile(
    tmp_config_dir: Path, monkeypatch
) -> None:
    """Loading quorus_mcp.server with QUORUS_PROFILE set should
    resolve identity from that profile, not the current pointer."""
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("current-one", {"relay_url": "https://wrong.test", "api_key": "sk_x"})
    pm.save("target", {
        "relay_url": "https://right.test",
        "api_key": "sk_y",
        "instance_name": "targetbot",
    })
    pm.set_current("current-one")

    monkeypatch.setenv("QUORUS_PROFILE", "target")
    # Required so server-module import doesn't raise on "no auth".
    # (Our profile supplies api_key, so nothing to set here.)

    # Import fresh — use importlib.reload to force re-eval of module-level
    # _config computation.
    import importlib

    import quorus_mcp.server as server_mod
    importlib.reload(server_mod)

    assert server_mod.RELAY_URL == "https://right.test"
    assert server_mod.API_KEY == "sk_y"
    assert server_mod.INSTANCE_NAME == "targetbot"


def test_codex_agent_command_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from quorus_cli import cli as cli_mod

    called = {}

    def fake_cmd(args) -> None:
        called["room"] = args.room
        called["suffix"] = args.suffix
        raise SystemExit(0)

    monkeypatch.setattr(cli_mod, "_cmd_codex_agent", fake_cmd)

    with patch.object(
        cli_mod.sys,
        "argv",
        ["quorus", "codex-agent", "medbuddy-sprint", "--suffix", "reviewer"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()

    assert exc.value.code == 0
    assert called == {"room": "medbuddy-sprint", "suffix": "reviewer"}


def test_spawn_codex_dispatches_to_codex_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from quorus_cli import cli as cli_mod

    called = {}

    def fake_spawn(room: str, name: str) -> None:
        called["room"] = room
        called["name"] = name

    monkeypatch.setattr(cli_mod, "_spawn_codex_agent", fake_spawn)
    monkeypatch.setattr(cli_mod, "_spawn_agent", lambda *args, **kwargs: None)

    with patch.object(
        cli_mod.sys,
        "argv",
        ["quorus", "spawn", "medbuddy-sprint", "reviewer", "--platform", "codex"],
    ):
        cli_mod.main()

    assert called == {"room": "medbuddy-sprint", "name": "reviewer"}


def test_spawn_defaults_to_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    from quorus_cli import cli as cli_mod

    called = {}

    def fake_spawn(room: str, name: str, relay_url: str, secret: str) -> None:
        called["room"] = room
        called["name"] = name
        called["relay_url"] = relay_url
        called["secret"] = secret

    monkeypatch.setattr(cli_mod, "_spawn_agent", fake_spawn)
    monkeypatch.setattr(cli_mod, "_spawn_codex_agent", lambda *args, **kwargs: None)

    with patch.object(cli_mod.sys, "argv", ["quorus", "spawn", "medbuddy-sprint", "builder"]):
        cli_mod.main()

    assert called["room"] == "medbuddy-sprint"
    assert called["name"] == "builder"


def test_resolve_explicit_agent_auth_registers_child_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from quorus_cli import cli as cli_mod

    monkeypatch.setattr(cli_mod, "_register_agent_identity", lambda **kwargs: "mct_child")
    saved = {}

    class FakePm:
        def current(self):
            return "default"

        def get(self, slug):
            assert slug == "default"
            return {"instance_name": "arav"}

        def save(self, slug, data):
            saved["slug"] = slug
            saved["data"] = data

    monkeypatch.setattr(cli_mod, "ProfileManager", lambda: FakePm())

    api_key, relay_secret = cli_mod._resolve_explicit_agent_auth(
        requested_name="arav-codex-reviewer",
        current_name="arav",
        relay_url="https://relay.test",
        api_key="mct_parent",
        relay_secret="",
    )

    assert api_key == "mct_child"
    assert relay_secret == ""
    assert saved["slug"] == "default"
    assert saved["data"]["agent_api_keys"]["arav-codex-reviewer"] == "mct_child"


def test_resolve_explicit_agent_auth_rejects_unrelated_name() -> None:
    from quorus_cli import cli as cli_mod

    with pytest.raises(ValueError):
        cli_mod._resolve_explicit_agent_auth(
            requested_name="other-person",
            current_name="arav",
            relay_url="https://relay.test",
            api_key="mct_parent",
            relay_secret="",
        )
