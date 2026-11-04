"""`quorus join` to another relay never replaces the user's own setup.

Joining a teammate's room used to write their relay over config.json; the next
start moved that over profiles/default.json, so the user's own key was lost and
every MCP client pointed at the teammate's relay (2026-11-04).
"""
from __future__ import annotations

import json

from quorus_cli import join_config

from quorus.profiles import ProfileManager

MINE = {"relay_url": "http://127.0.0.1:8080", "instance_name": "arav", "api_key": "mine"}


def _theirs(name: str = "arav") -> dict:
    return {"relay_url": "https://quorus.example.com", "instance_name": name,
            "api_key": "theirs"}


def _setup(tmp_path, data: dict = MINE) -> ProfileManager:
    pm = ProfileManager(tmp_path)
    pm.save("default", data)
    pm.set_current("default")
    return pm


def test_first_join_becomes_the_setup(tmp_path) -> None:
    saved = join_config.save(_theirs(), tmp_path)
    assert (saved.slug, saved.current) == (None, True)
    assert json.loads((tmp_path / "config.json").read_text())["api_key"] == "theirs"


def test_joining_another_relay_keeps_the_current_setup(tmp_path) -> None:
    pm = _setup(tmp_path)
    saved = join_config.save(_theirs(), tmp_path)
    assert (saved.slug, saved.current) == ("quorus-example-com", False)
    assert pm.current() == "default" and pm.get("default") == MINE
    assert pm.get("quorus-example-com")["api_key"] == "theirs"
    assert saved.previous == MINE


def test_a_flat_config_survives_a_join_to_another_relay(tmp_path) -> None:
    (tmp_path / "config.json").write_text(json.dumps(MINE))
    join_config.save(_theirs(), tmp_path)
    pm = ProfileManager(tmp_path)
    assert pm.current() == "default" and pm.get("default") == MINE
    assert pm.get("quorus-example-com")["relay_url"] == "https://quorus.example.com"


def test_same_relay_and_name_refreshes_the_current_profile(tmp_path) -> None:
    pm = _setup(tmp_path, {**MINE, "workspace_label": "home"})
    saved = join_config.save({**MINE, "relay_url": MINE["relay_url"] + "/",
                              "api_key": "new"}, tmp_path)
    assert (saved.slug, saved.current) == ("default", True)
    assert pm.get("default")["api_key"] == "new"
    assert pm.get("default")["workspace_label"] == "home"


def test_a_new_name_on_the_same_relay_is_its_own_profile(tmp_path) -> None:
    pm = _setup(tmp_path)
    saved = join_config.save({**MINE, "instance_name": "erin", "api_key": "e"}, tmp_path)
    assert saved.current is False and pm.get("default") == MINE
    assert pm.get(saved.slug)["instance_name"] == "erin"


def test_a_switched_secret_setup_drops_the_old_credential(tmp_path) -> None:
    pm = _setup(tmp_path, {**MINE, "api_key": "", "relay_secret": "s"})
    join_config.save(MINE, tmp_path)
    assert "relay_secret" not in pm.get("default")


def test_profile_names_dont_collide(tmp_path) -> None:
    pm = _setup(tmp_path)
    first = join_config.save(_theirs("erin"), tmp_path)
    second = join_config.save(_theirs("gil"), tmp_path)
    again = join_config.save({**_theirs("erin"), "api_key": "rotated"}, tmp_path)
    assert (first.slug, second.slug, again.slug) == (
        "quorus-example-com", "quorus-example-com-2", "quorus-example-com")
    assert pm.get("quorus-example-com")["api_key"] == "rotated"
    assert pm.get("quorus-example-com-2")["instance_name"] == "gil"
