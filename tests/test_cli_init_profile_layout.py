"""``quorus init`` must not flatten a hub-created profile layout.

A first-run ``quorus`` (the hub) stores config as a pointer
(``config.json`` = ``{current, profiles}``) plus ``profiles/*.json``. Re-running
``quorus init`` used to write the flat shape straight over the pointer: the
real profile was orphaned, and the next ``load_config()`` migrated the flat
blob into a fresh ``default`` profile — silently switching auth to whatever
secret was just typed. These tests pin the pointer-preserving path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from quorus_cli.cli import _cmd_init, _config_is_profile_pointer

from quorus.config import ConfigManager
from quorus.profiles import ProfileManager

REAL_SECRET = "REAL-SECRET-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _make_init_args(name="alice", relay_url="http://localhost:8080", secret="TYPED-WRONG"):
    args = MagicMock()
    args.name = name
    args.relay_url = relay_url
    args.secret = secret
    args.api_key = None
    return args


@pytest.fixture
def hub_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HOME wired the way the first-run hub leaves it."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / ".quorus"))
    monkeypatch.setattr("quorus.cli.shutil.which", lambda _: None)
    monkeypatch.setattr("quorus.cli._is_platform_installed", lambda p: False)
    monkeypatch.setattr("quorus.cli.httpx.get", lambda *a, **kw: MagicMock(status_code=200))
    return tmp_path


def _write_hub_layout(home: Path, slug: str) -> Path:
    config_dir = home / ".quorus"
    (config_dir / "profiles").mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"current": slug, "profiles": [slug]})
    )
    profile = config_dir / "profiles" / f"{slug}.json"
    profile.write_text(
        json.dumps(
            {
                "instance_name": slug,
                "relay_url": "http://localhost:8080",
                "relay_secret": REAL_SECRET,
                "push_notification_method": "notifications/claude/channel",
                "push_notification_channel": "quorus",
            }
        )
    )
    return profile


def test_config_is_profile_pointer_distinguishes_shapes(tmp_path: Path) -> None:
    pointer = tmp_path / "pointer.json"
    pointer.write_text('{"current": "default", "profiles": ["default"]}')
    assert _config_is_profile_pointer(pointer)

    flat = tmp_path / "flat.json"
    flat.write_text('{"relay_url": "http://localhost:8080", "instance_name": "alice"}')
    assert not _config_is_profile_pointer(flat)

    for raw in ("{}", "[]", "not json"):
        bad = tmp_path / "bad.json"
        bad.write_text(raw)
        assert not _config_is_profile_pointer(bad)


def test_init_over_hub_pointer_keeps_pointer_and_updates_profile(hub_home: Path) -> None:
    profile = _write_hub_layout(hub_home, "default")
    config_path = hub_home / ".quorus" / "config.json"

    _cmd_init(_make_init_args())

    pointer = json.loads(config_path.read_text())
    assert pointer == {"current": "default", "profiles": ["default"]}, (
        "init flattened the pointer the hub wrote"
    )
    assert json.loads(profile.read_text())["relay_secret"] == "TYPED-WRONG"


def test_init_over_hub_pointer_does_not_hijack_current_profile(hub_home: Path) -> None:
    """The reported bug: a non-'default' profile got replaced by a bogus default."""
    profile = _write_hub_layout(hub_home, "work")
    config_path = hub_home / ".quorus" / "config.json"

    _cmd_init(_make_init_args(name="alice"))

    pm = ProfileManager(config_dir=hub_home / ".quorus")
    assert pm.current() == "work", "init switched the active profile"
    assert pm.list() == ["work"], "init created a stray profile"
    assert json.loads(profile.read_text())["relay_secret"] == "TYPED-WRONG"
    assert "relay_url" not in json.loads(config_path.read_text())

    # A later load must read the profile we just wrote, not migrate a flat blob.
    loaded = ConfigManager().load()
    assert loaded["instance_name"] == "alice"
    assert loaded["relay_secret"] == "TYPED-WRONG"
    assert not (hub_home / ".quorus" / "profiles" / "default.json").exists()


def test_init_still_writes_flat_config_without_a_pointer(hub_home: Path) -> None:
    """Back-compat: a fresh install keeps the legacy flat shape."""
    _cmd_init(_make_init_args())

    cfg = json.loads((hub_home / ".quorus" / "config.json").read_text())
    assert cfg["relay_url"] == "http://localhost:8080"
    assert cfg["relay_secret"] == "TYPED-WRONG"


def test_init_warns_it_is_replacing_profile_credentials(hub_home: Path, capsys) -> None:
    _write_hub_layout(hub_home, "default")

    _cmd_init(_make_init_args())

    out = " ".join(capsys.readouterr().out.split())
    assert "Updating profile 'default'" in out
    assert "credentials are replaced" in out
