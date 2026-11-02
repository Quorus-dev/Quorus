"""``quorus profile list | use | show`` (FRICTION 2026-11-02)."""
from __future__ import annotations

import argparse
import json

import pytest
from quorus_cli import profile_cmd, ui

from quorus.profiles import ProfileManager

SECRET = "synthetic-relay-secret-value"


@pytest.fixture
def pm(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cfg = home / ".quorus"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(ui.console, "width", 200)
    manager = ProfileManager(cfg)
    manager.save("default", {"instance_name": "arav", "relay_url": "http://127.0.0.1:8787",
                             "relay_secret": SECRET})
    manager.save("fly", {"instance_name": "arav",
                         "relay_url": "https://user:pw@relay.example.com?token=abc",
                         "relay_secret": SECRET, "api_key": "quorus_pk_synthetic"})
    manager.set_current("default")
    return manager


def run(action=None, name=None):
    profile_cmd.run(argparse.Namespace(action=action, name=name))


def test_list_marks_current_and_hides_secrets(pm, capsys):
    run()
    out = capsys.readouterr().out
    assert "* default" in out and "  fly" in out
    assert "https://relay.example.com" in out
    for leaked in (SECRET, "quorus_pk_synthetic", "user:pw", "token=abc"):
        assert leaked not in out


def test_use_switches_current_only(pm, capsys):
    before = json.loads((pm.profiles_dir / "fly.json").read_text())
    run("use", "fly")
    assert pm.current() == "fly"
    assert json.loads((pm.profiles_dir / "fly.json").read_text()) == before
    assert "Using profile fly" in capsys.readouterr().out


def test_use_unknown_profile_fails_and_keeps_current(pm):
    with pytest.raises(SystemExit) as exc:
        run("use", "nope")
    assert exc.value.code == 1 and pm.current() == "default"


def test_use_rejects_path_like_names(pm):
    with pytest.raises(SystemExit):
        run("use", "../config")
    assert pm.current() == "default"


def test_use_without_name_is_usage_error(pm):
    with pytest.raises(SystemExit) as exc:
        run("use")
    assert exc.value.code == 2


def test_show_reports_secrets_as_set_never_values(pm, capsys):
    run("show", "fly")
    out = capsys.readouterr().out
    assert "relay_secret   set" in out and "api_key        set" in out
    assert "agent_api_keys not set" in out
    assert SECRET not in out and "quorus_pk_synthetic" not in out


def test_show_defaults_to_current(pm, capsys):
    run("show")
    assert "profile        default (current)" in capsys.readouterr().out


def test_list_with_no_profiles_points_at_init(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / ".quorus"))
    with pytest.raises(SystemExit):
        run()
    assert "quorus init" in capsys.readouterr().out + capsys.readouterr().err
