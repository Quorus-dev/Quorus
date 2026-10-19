"""A harness that can't write says so once and stops (0.6.0 stranger blocker).

Codex in `default` mode runs in its own read-only sandbox and opencode has no
edit or shell tools, so "fix the bug" was handed back and forth ten times.
Now the wake instructions tell the agent, on the owner's own turns only, that
this turn can't edit here, with the command the owner can run. An untrusted
sender never sees that command (codex, review of the plan)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd_cw", _ROOT / "scripts" / "reflexd.py")
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd_cw"] = reflexd
_spec.loader.exec_module(reflexd)
wake = reflexd.reflexd_wake


@pytest.fixture(autouse=True)
def _no_codex_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))  # no config.toml
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "")


def test_codex_effective_sandbox_follows_mode_env_then_config(monkeypatch, tmp_path) -> None:
    assert wake.codex_effective_sandbox("autonomous") == "workspace-write"
    assert wake.codex_effective_sandbox("manual") == "read-only"
    assert wake.codex_effective_sandbox("default") == "read-only"  # codex's headless default
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "workspace-write")
    assert wake.codex_effective_sandbox("default") == "workspace-write"
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "")
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text('model = "x"\nsandbox_mode = "workspace-write"\n')
    assert wake.codex_effective_sandbox("default") == "workspace-write"
    assert wake.codex_effective_sandbox("manual") == "read-only"  # the room mode wins


@pytest.mark.parametrize("toml,expected", [
    ("sandbox_mode = 'workspace-write'\n", "workspace-write"),  # valid single quotes
    ('[profiles.other]\nsandbox_mode = "workspace-write"\n', "read-only"),  # inactive
    ('profile = "work"\nsandbox_mode = "read-only"\n[profiles.work]\n'
     'sandbox_mode = "workspace-write"\n', "workspace-write"),  # the active profile wins
    ('profile = "work"\nsandbox_mode = "workspace-write"\n[profiles.other]\n'
     'sandbox_mode = "read-only"\n', "workspace-write"),  # top level when the profile is silent
    ('sandbox_mode = "workspace-write"\n[broken\n', "read-only"),  # unparseable: unknown
    ("sandbox_mode = 3\n", "read-only"),
])
def test_codex_config_is_parsed_as_toml_in_the_active_scope(tmp_path, toml, expected) -> None:
    """Codex's repros on a5b0f90e: a regex misread single-quoted values and counted
    a value inside an inactive profile."""
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(toml)
    assert wake.codex_effective_sandbox("default") == expected


@pytest.mark.parametrize("harness,mode,expected", [
    ("codex", "default", True), ("codex", "autonomous", False), ("codex", "manual", True),
    ("opencode", "default", True), ("opencode", "autonomous", False),
    ("opencode", "manual", True),
    ("claude", "default", False),  # the owner's own settings: unknown, so no claim
    ("claude", "autonomous", False), ("claude", "manual", False),
    ("claude", "readonly", True), ("gemini", "default", False),
])
def test_cannot_write_only_claims_what_is_known(harness, mode, expected) -> None:
    assert wake.cannot_write(harness, mode) is expected


def _daemon(monkeypatch, tmp_path, mode: str = "default"):
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    monkeypatch.setattr(reflexd, "mode_for", lambda room: mode)
    monkeypatch.setattr(reflexd, "trust_for", lambda room: "owner")
    monkeypatch.setattr(reflexd, "lead_for", lambda room: None)
    return reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                                 participant_name="arav-codex",
                                                 runtime_dir=tmp_path / "rt"))


def _prompt(d, *, sender: str, trusted: bool, harness: str = "codex") -> str:
    env = {"id": "w", "from_name": sender, "room": "build", "content": "fix calc.py"}
    return d._build_prompt(env, [], has_workspace=True, trusted=trusted, harness=harness)


def test_the_owners_turn_is_told_once_with_the_command(monkeypatch, tmp_path) -> None:
    text = _prompt(_daemon(monkeypatch, tmp_path), sender="arav", trusted=True)
    assert "You can't edit files or run commands in this turn" in text
    assert "`quorus agent add codex --room build --mode autonomous`" in text
    assert "say that once, in one line, and stop" in text
    assert "Don't hand the task to a teammate agent" in text


def test_an_untrusted_sender_never_sees_the_command(monkeypatch, tmp_path) -> None:
    for mode in ("default", "autonomous", "manual"):
        text = _prompt(_daemon(monkeypatch, tmp_path, mode), sender="mallory", trusted=False)
        assert "--mode autonomous" not in text
        assert "You can't edit files or run commands in this turn" not in text


def test_no_claim_when_the_harness_may_write(monkeypatch, tmp_path) -> None:
    d = _daemon(monkeypatch, tmp_path, "autonomous")
    assert "can't edit files" not in _prompt(d, sender="arav", trusted=True)
    d = _daemon(monkeypatch, tmp_path, "default")
    assert "can't edit files" not in _prompt(d, sender="arav", trusted=True, harness="claude")


def test_every_turn_is_told_not_to_delegate_a_permission_problem(monkeypatch, tmp_path) -> None:
    text = _prompt(_daemon(monkeypatch, tmp_path, "autonomous"), sender="arav", trusted=True)
    assert "don't hand the task to a teammate agent for that reason" in text
