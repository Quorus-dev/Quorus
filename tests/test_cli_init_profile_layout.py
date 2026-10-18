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

import httpx
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


def test_init_flat_overwrite_warning_names_what_is_replaced(hub_home: Path, capsys) -> None:
    """Finding #5: the old line just said "overwriting" — name what is gone."""
    config_dir = hub_home / ".quorus"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        '{"relay_url": "http://localhost:8080", "relay_secret": "KEEP"}'
    )

    _cmd_init(_make_init_args())

    out = " ".join(capsys.readouterr().out.split())
    assert "config already exists" in out
    assert "relay URL and credentials are replaced" in out


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


def _relay_get(*, health: int = 200, rooms: int = 200):
    """httpx.get stand-in: /health and /rooms can answer differently."""

    def _get(url, **_kw):
        return MagicMock(status_code=rooms if url.endswith("/rooms") else health)

    return _get


def test_init_rejected_secret_leaves_the_profile_untouched(hub_home, monkeypatch, capsys) -> None:
    """A mistyped --secret must not destroy the working one (review 2026-10-17).

    Verified before writing: before the fix the profile was overwritten with
    the bad secret and nothing on disk still held the real one.
    """
    profile = _write_hub_layout(hub_home, "default")
    before = profile.read_text()
    monkeypatch.setattr("quorus.cli.httpx.get", _relay_get(health=200, rooms=401))

    with pytest.raises(SystemExit) as caught:
        _cmd_init(_make_init_args())

    assert caught.value.code == 3
    assert profile.read_text() == before, "the rejected run rewrote the profile"
    assert json.loads(profile.read_text())["relay_secret"] == REAL_SECRET
    assert "rejected that secret" in " ".join(capsys.readouterr().out.split())
    # Nothing else should have been written either (no MCP registration).
    assert not (hub_home / ".mcp.json").exists()
    assert not (hub_home / ".claude.json").exists()


def test_init_backs_up_the_profile_before_overwriting_it(hub_home, monkeypatch) -> None:
    """Relay down → init warns and writes anyway, so keep the old secret.

    Without the copy, a typo made while the relay is offline destroyed the last
    working secret with nothing left to recover from (review 2026-10-17).
    """
    profile = _write_hub_layout(hub_home, "default")
    before = profile.read_text()

    def _unreachable(*_a, **_kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("quorus.cli.httpx.get", _unreachable)

    _cmd_init(_make_init_args())

    backup = profile.with_suffix(".json.bak")
    assert backup.exists(), "no copy of the previous secret was kept"
    assert backup.read_text() == before
    assert (backup.stat().st_mode & 0o777) == 0o600, "backup holds a secret"
    assert json.loads(profile.read_text())["relay_secret"] == "TYPED-WRONG"


def test_init_never_sends_an_api_key_through_a_redirect(hub_home, monkeypatch) -> None:
    """A 307/308 re-sends the POST body, so the API key must not ride it.

    Covers every init-path POST that puts the key in the body, not just the
    pre-write check: the tenant lookup posts the same key to the same endpoint.
    """
    calls: list[tuple[str, dict]] = []

    def _post(url, **kw):
        calls.append((url, kw))
        return MagicMock(status_code=307)

    monkeypatch.setattr("quorus.cli.httpx.get", _relay_get(health=200))
    monkeypatch.setattr("quorus.cli.httpx.post", _post)
    monkeypatch.setattr("quorus.cli._register_agent_identity", lambda **_: None)
    args = _make_init_args(secret=None)
    args.api_key = "mct_parent"

    _cmd_init(args)

    leaks = [
        url
        for url, kw in calls
        if isinstance(kw.get("json"), dict)
        and kw["json"].get("api_key")
        and kw.get("follow_redirects") is True
    ]
    assert not leaks, f"the API key would follow a redirect to: {leaks}"


def test_init_rejected_api_key_leaves_the_profile_untouched(hub_home, monkeypatch) -> None:
    """The API-key path had the same write-first data loss as the secret path."""
    profile = _write_hub_layout(hub_home, "default")
    before = profile.read_text()
    monkeypatch.setattr("quorus.cli.httpx.get", _relay_get(health=200))
    monkeypatch.setattr(
        "quorus.cli.httpx.post",
        lambda url, **_kw: MagicMock(status_code=401 if url.endswith("/v1/auth/token") else 200),
    )
    args = _make_init_args(secret=None)
    args.api_key = "mct_TYPED_WRONG"

    with pytest.raises(SystemExit) as caught:
        _cmd_init(args)

    assert caught.value.code == 3
    assert profile.read_text() == before, "the rejected run rewrote the profile"
    assert not (hub_home / ".mcp.json").exists()


def test_init_rejected_secret_leaves_flat_config_untouched(hub_home, monkeypatch) -> None:
    config_dir = hub_home / ".quorus"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text('{"relay_url": "http://localhost:8080", "relay_secret": "KEEP"}')
    monkeypatch.setattr("quorus.cli.httpx.get", _relay_get(health=200, rooms=403))

    with pytest.raises(SystemExit) as caught:
        _cmd_init(_make_init_args())

    assert caught.value.code == 3
    assert json.loads(config_path.read_text())["relay_secret"] == "KEEP"


def test_init_merges_into_the_existing_profile(hub_home, monkeypatch) -> None:
    """Keys a later init doesn't set must survive, and the stale credential goes."""
    profile = _write_hub_layout(hub_home, "work")
    stored = json.loads(profile.read_text())
    stored["agent_api_keys"] = {"claude": "mct_claude_test"}
    stored["api_key"] = "mct_old_parent"
    stored["custom_key"] = "keep-me"
    profile.write_text(json.dumps(stored))
    monkeypatch.setattr("quorus.cli.httpx.get", _relay_get(health=200, rooms=200))

    _cmd_init(_make_init_args())

    merged = json.loads(profile.read_text())
    assert merged["agent_api_keys"] == {"claude": "mct_claude_test"}
    assert merged["custom_key"] == "keep-me"
    assert merged["relay_secret"] == "TYPED-WRONG"
    assert "api_key" not in merged, "the superseded api_key would still win"



def _agent_identity_dir(home: Path) -> Path:
    """What the wake daemon writes for an agent (reflexd_wake.write_agent_config_dir)."""
    import importlib.util
    import sys

    path = Path(__file__).resolve().parent.parent / "scripts" / "reflexd_wake.py"
    spec = importlib.util.spec_from_file_location("reflexd_wake_u3", path)
    assert spec is not None and spec.loader is not None
    wake = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("reflexd_wake_u3", wake)
    spec.loader.exec_module(wake)
    return wake.write_agent_config_dir(home / ".quorus", relay_url="http://localhost:8787",
                                       api_key=REAL_SECRET, participant="arav-claude",
                                       legacy=True)


def test_init_never_overwrites_an_agents_own_identity(hub_home: Path) -> None:
    """U3 (0.5.0 sweep): an agent ran `quorus init` in a throwaway HOME, but its
    inherited QUORUS_CONFIG_DIR beat HOME and init replaced its credentials."""
    root = _agent_identity_dir(hub_home)
    before = (root / "profiles" / "arav-claude.json").read_bytes()
    args = _make_init_args(name="arav")
    args.config_dir = None
    args.force = False
    with pytest.raises(SystemExit) as exc:
        _cmd_init(args)
    assert exc.value.code == 5
    assert (root / "profiles" / "arav-claude.json").read_bytes() == before
    assert not (root / "profiles" / "arav-claude.json.bak").exists()


def test_init_force_or_another_dir_still_works(hub_home: Path, tmp_path: Path) -> None:
    root = _agent_identity_dir(hub_home)
    args = _make_init_args(name="arav")
    args.config_dir = str(tmp_path / "elsewhere")
    args.force = False
    _cmd_init(args)  # an explicit other dir is fine
    assert json.loads((tmp_path / "elsewhere" / "config.json").read_text())
    args.config_dir = None
    args.force = True
    _cmd_init(args)
    assert "TYPED-WRONG" in (root / "profiles" / "arav-claude.json").read_text()
