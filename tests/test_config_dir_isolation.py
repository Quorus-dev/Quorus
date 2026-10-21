"""Two daemons with different QUORUS_CONFIG_DIRs share nothing (2c-3a).

Dogfood plan §2: the dogfood agents run beside the team's daemons on one machine and
one HOME (a second HOME would also move the harness CLIs' own sign-in; Claude on
2c-3). Each config dir gets its own room bindings (and so its own handoff switch),
runtime dir, handoff journals, hold markers, daemon lock and harness sessions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fresh(name: str, path: Path):
    """A fresh copy of a module, so import-time paths see this test's env, and the
    suite-wide isolation patch on ``quorus.runtime.bindings`` doesn't apply."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_config_dir_follows_quorus_config_dir(monkeypatch, tmp_path) -> None:
    bindings = _fresh("bindings_fresh", ROOT / "quorus" / "runtime" / "bindings.py")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("QUORUS_CONFIG_DIR", raising=False)
    assert bindings.config_dir() == tmp_path / "home" / ".quorus"
    assert bindings.bindings_path() == tmp_path / "home" / ".quorus" / "room-bindings.json"
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "dogfood"))
    assert bindings.bindings_path() == tmp_path / "dogfood" / "room-bindings.json"
    # no legacy fallback: an old ~/.murmur never captures the bindings
    (tmp_path / "home" / ".murmur").mkdir(parents=True)
    (tmp_path / "home" / ".murmur" / "config.json").write_text("{}")
    monkeypatch.delenv("QUORUS_CONFIG_DIR")
    assert bindings.config_dir() == tmp_path / "home" / ".quorus"


def _daemon_under(monkeypatch, tmp_path: Path, tag: str, participant: str):
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / tag))
    mod = _fresh(f"reflexd_{tag}", ROOT / "scripts" / "reflexd.py")
    cfg = mod.ReflexdConfig(relay_url="http://r", api_key="k", participant_name=participant,
                            spawn_enabled=False)
    return mod, mod.Reflexd(cfg)


def test_two_daemons_with_different_config_dirs_share_nothing(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from quorus.runtime import handoff_release, handoff_runtime

    team_mod, team = _daemon_under(monkeypatch, tmp_path, "team", "arav-codex")
    dog_mod, dog = _daemon_under(monkeypatch, tmp_path, "dogfood", "arav-handoff-codex")
    wt = SimpleNamespace(repo=tmp_path)
    for mod, daemon, tag in ((team_mod, team, "team"), (dog_mod, dog, "dogfood")):
        mine = tmp_path / tag
        assert mod.DEFAULT_RUNTIME_DIR == mine / "runtime"
        assert daemon.config.runtime_dir == mine / "runtime"
        assert daemon._unfinished_dir.is_relative_to(mine)  # hold markers
        assert handoff_runtime.journal(daemon, "room", wt).path.is_relative_to(mine)
        assert handoff_release.daemon_lock_path(daemon.config.runtime_dir,
                                                daemon.config.participant_name
                                                ).is_relative_to(mine)
        # resolved at call time, in that daemon's own process env
        monkeypatch.setenv("QUORUS_CONFIG_DIR", str(mine))
        assert mod._sessions_path(daemon.config.participant_name).is_relative_to(mine)
        assert mod.DEFAULT_LOG_PATH.is_relative_to(mine)
    assert not team.config.runtime_dir.is_relative_to(tmp_path / "dogfood")


def test_the_cli_config_resolver_keeps_its_legacy_fallback(monkeypatch, tmp_path) -> None:
    """Codex on 90321f74: importing ``config_dir as _config_dir`` into cli.py replaced
    the CLI's own resolver, so with only a legacy ~/.murmur config, reads went to
    .murmur and writes to .quorus. The daemon-state helper has its own name now."""
    import quorus_cli.cli as cli

    from quorus.runtime import bindings

    assert cli._config_dir is not bindings.config_dir
    home = tmp_path / "home"
    (home / ".murmur").mkdir(parents=True)
    (home / ".murmur" / "config.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("QUORUS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MCP_TUNNEL_CONFIG_DIR", raising=False)
    from quorus import config

    monkeypatch.setattr(config, "_default_config_dir", lambda: home / ".quorus",
                        raising=False)
    assert cli._config_dir() == home / ".murmur"  # the CLI's legacy resolution, unchanged


def test_an_agent_session_never_writes_the_owners_bindings(monkeypatch, tmp_path) -> None:
    """Claude on 90321f74: agent turns run with QUORUS_CONFIG_DIR set to the agent's
    own dir, so `quorus room bind --handoff on` there lands in the agent's file."""
    bindings = _fresh("bindings_agent", ROOT / "quorus" / "runtime" / "bindings.py")
    owner = tmp_path / "home" / ".quorus"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(owner / "runtime" / "agents" / "arav-claude"))
    path = bindings.bindings_path()
    assert path.is_relative_to(owner / "runtime" / "agents" / "arav-claude")
    assert path != owner / "room-bindings.json"
