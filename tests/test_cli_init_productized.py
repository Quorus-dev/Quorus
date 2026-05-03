"""Tests for the productized ``quorus init`` flow.

Covers the post-Phase-1 init contract: a single command moves the user from
fresh install -> per-platform agent keys minted, ``human`` profile created,
``reflexd-manager`` daemonized, optional launchd opt-in, and a wake-smoke
that proves an @-mention round-trips. None of these tests touch the network
or spawn a real daemon — every external call is mocked at the seam closest
to the boundary, so the suite stays under a few seconds in total and keeps
working in cold-install CI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_init_args(
    name: str = "arav",
    relay_url: str = "https://relay.test",
    api_key: str = "mct_parent_test",
    secret: str | None = None,
    *,
    no_autostart: bool = True,
    no_smoke: bool = True,
    no_launchd: bool = True,
    auto_launchd: bool = False,
    config_dir: str | None = None,
) -> Any:
    args = MagicMock()
    args.name = name
    args.relay_url = relay_url
    args.secret = secret
    args.api_key = api_key
    args.no_autostart = no_autostart
    args.no_smoke = no_smoke
    args.no_launchd = no_launchd
    args.auto_launchd = auto_launchd
    args.config_dir = config_dir
    return args


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Reroute ~/.quorus and ~/.* config writes under tmp_path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / ".quorus"))
    monkeypatch.delenv("MCP_TUNNEL_CONFIG_DIR", raising=False)
    # Don't actually call the relay during init.
    health_resp = MagicMock(status_code=200)
    monkeypatch.setattr("quorus.cli.httpx.get", lambda *a, **kw: health_resp)
    # Default-deny: pretend no MCP clients are installed (so writers don't try
    # to touch ~/.codex etc). Individual tests may override.
    monkeypatch.setattr("quorus.cli._is_platform_installed", lambda p: False)
    return tmp_path


def _stub_register_agent_identity(monkeypatch: pytest.MonkeyPatch, calls: list) -> None:
    """Replace _register_agent_identity to record each invocation."""

    def _fake(*, relay_url: str, parent_api_key: str, suffix: str) -> str:
        calls.append(suffix)
        return f"mct_{suffix}_test"

    monkeypatch.setattr("quorus.cli._register_agent_identity", _fake)


def _stub_human_signup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    success: bool = True,
    api_key: str = "mct_human_test",
    tenant_id: str = "11111111-1111-1111-1111-111111111111",
) -> None:
    """Stub the human-signup flow (token exchange + usage + signup POST)."""

    def _fake_post(url: str, **_kw):
        resp = MagicMock()
        if url.endswith("/v1/auth/token"):
            resp.status_code = 200
            resp.json.return_value = {"token": "jwt-test"}
        elif url.endswith("/v1/auth/signup"):
            resp.status_code = 200 if success else 500
            resp.json.return_value = {
                "tenant_slug": "test-ws",
                "participant_name": "arav",
                "key_prefix": "mct_human",
                "key_id": "k1",
                "next_steps": "...",
                "relay_url": "https://relay.test",
                "api_key": api_key if success else None,
            }
        else:
            resp.status_code = 200
            resp.json.return_value = {}
        return resp

    def _fake_get(url: str, **_kw):
        resp = MagicMock()
        if url.endswith("/v1/usage"):
            resp.status_code = 200
            resp.json.return_value = {"tenant_id": tenant_id}
        else:
            resp.status_code = 200
            resp.json.return_value = {}
        return resp

    monkeypatch.setattr("quorus.cli.httpx.post", _fake_post)
    monkeypatch.setattr("quorus.cli.httpx.get", _fake_get)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_init_mints_four_agent_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """register-agent must be invoked once per canonical platform suffix."""
    from quorus.cli import _cmd_init

    calls: list[str] = []
    _stub_register_agent_identity(monkeypatch, calls)
    _stub_human_signup(monkeypatch)

    _cmd_init(_make_init_args())

    # Four canonical platforms — all must appear at least once.
    assert set(calls) >= {"claude", "codex", "gemini", "cursor"}


def test_init_writes_agent_api_keys_to_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Init must persist agent_api_keys for every minted suffix."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    _cmd_init(_make_init_args())

    # The flat config carries the keys (legacy readers).
    config_path = tmp_path / ".quorus" / "config.json"
    pointer_or_flat = json.loads(config_path.read_text())
    keys: dict[str, str] = {}
    if "agent_api_keys" in pointer_or_flat:
        keys = pointer_or_flat["agent_api_keys"]
    else:
        # Pointer file — pull keys from the profile.
        for slug in pointer_or_flat.get("profiles", []):
            profile_path = tmp_path / ".quorus" / "profiles" / f"{slug}.json"
            if profile_path.exists():
                profile_data = json.loads(profile_path.read_text())
                if profile_data.get("agent_api_keys"):
                    keys = profile_data["agent_api_keys"]
                    break

    expected = {"arav-claude", "arav-codex", "arav-gemini", "arav-cursor"}
    assert expected <= set(keys.keys())
    for v in keys.values():
        assert v.startswith("mct_")


def test_init_creates_human_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Init signs up the human owner and writes profiles/human.json."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    _cmd_init(_make_init_args())

    human_path = tmp_path / ".quorus" / "profiles" / "human.json"
    assert human_path.exists(), "human profile must be auto-created"

    human = json.loads(human_path.read_text())
    assert human["instance_name"] == "arav"
    assert human["api_key"].startswith("mct_human")
    assert human["relay_url"] == "https://relay.test"


def test_init_starts_reflexd_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """When --no-autostart is absent, init must Popen the supervisor."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    captured = {"args": None}

    class _FakeProc:
        pid = 12345

        def poll(self):
            return None

    def _fake_popen(*args, **kwargs):
        captured["args"] = args[0]
        return _FakeProc()

    monkeypatch.setattr("quorus.cli.subprocess.Popen", _fake_popen)
    # Pretend the supervisor came up cleanly.
    monkeypatch.setattr("quorus.cli._supervisor_alive", lambda: False)
    monkeypatch.setattr("quorus.cli._read_supervisor_pid", lambda: 12345)
    monkeypatch.setattr("quorus.cli._pid_is_alive", lambda pid: True)

    _cmd_init(_make_init_args(no_autostart=False))

    cmd = captured["args"]
    assert cmd is not None, "Popen must be called for the supervisor"
    # Either path is acceptable: the `quorus` binary or the python -m form.
    joined = " ".join(cmd)
    assert "reflexd-manager" in joined and "start" in joined


def test_init_skips_autostart_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """With --no-autostart, the supervisor must NOT be started."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    def _explode(*_a, **_kw):  # pragma: no cover — must not be reached
        pytest.fail("Popen must not be called when --no-autostart is set")

    monkeypatch.setattr("quorus.cli.subprocess.Popen", _explode)

    _cmd_init(_make_init_args(no_autostart=True))


def test_init_runs_wake_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --no-smoke, init must invoke the wake-smoke helper."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    seen = {"called": False}

    def _fake_smoke(**kwargs):
        seen["called"] = True
        return True, 0.42, f"reply from @{kwargs['base_name']}-claude"

    monkeypatch.setattr("quorus.cli._init_run_wake_smoke", _fake_smoke)

    _cmd_init(_make_init_args(no_smoke=False, no_autostart=True))

    assert seen["called"], "wake-smoke must run when --no-smoke is absent"


def test_init_skips_smoke_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """With --no-smoke, the wake-smoke helper must not fire."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    def _explode(**_kw):  # pragma: no cover
        pytest.fail("wake-smoke must not run with --no-smoke")

    monkeypatch.setattr("quorus.cli._init_run_wake_smoke", _explode)

    _cmd_init(_make_init_args(no_smoke=True))


def test_init_macos_prompts_for_launchd(monkeypatch: pytest.MonkeyPatch) -> None:
    """On macOS + interactive TTY (no flags), init must prompt for launchd install."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    asked: dict[str, Any] = {"call_count": 0}

    def _fake_ask(*_a, **_kw):
        asked["call_count"] += 1
        return False  # user declines — don't actually install

    monkeypatch.setattr("quorus.cli.Confirm.ask", _fake_ask)

    _cmd_init(
        _make_init_args(no_launchd=False, auto_launchd=False, no_autostart=True)
    )

    assert asked["call_count"] == 1, "Confirm.ask must fire exactly once on darwin TTY"


def test_init_auto_launchd_flag_skips_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """--auto-launchd installs without asking."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def _no_prompt(*_a, **_kw):  # pragma: no cover
        pytest.fail("Confirm.ask must NOT fire under --auto-launchd")

    monkeypatch.setattr("quorus.cli.Confirm.ask", _no_prompt)

    installed = {"called": False}

    def _fake_install(_args):
        installed["called"] = True

    monkeypatch.setattr("quorus.cli._manager_install_launchd", _fake_install)

    # Note: must set no_launchd=False so the helper isn't short-circuited.
    _cmd_init(
        _make_init_args(auto_launchd=True, no_launchd=False, no_autostart=True)
    )

    assert installed["called"], "launchd installer must run under --auto-launchd"


def test_init_no_launchd_flag_skips_prompt_and_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-launchd suppresses BOTH the prompt and the install."""
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def _no_prompt(*_a, **_kw):  # pragma: no cover
        pytest.fail("Confirm.ask must NOT fire under --no-launchd")

    monkeypatch.setattr("quorus.cli.Confirm.ask", _no_prompt)

    def _no_install(_args):  # pragma: no cover
        pytest.fail("launchd installer must NOT run under --no-launchd")

    monkeypatch.setattr("quorus.cli._manager_install_launchd", _no_install)

    _cmd_init(_make_init_args(no_launchd=True, no_autostart=True))


def test_init_idempotent_existing_human_profile_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-running init with an existing human.json must not silently overwrite.

    In a non-interactive context (the default in tests), the existing file
    is preserved. The test asserts the file's contents are unchanged after
    a second init pass.
    """
    from quorus.cli import _cmd_init

    _stub_register_agent_identity(monkeypatch, [])
    _stub_human_signup(monkeypatch, api_key="mct_NEW_human")

    # Pre-create a human profile with a sentinel value.
    profiles_dir = tmp_path / ".quorus" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "instance_name": "arav",
        "api_key": "mct_PRESERVED_test",
        "relay_url": "https://relay.test",
    }
    (profiles_dir / "human.json").write_text(json.dumps(sentinel))

    # Pretend the prompt is non-interactive so we hit the "keep existing" branch.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    _cmd_init(_make_init_args())

    after = json.loads((profiles_dir / "human.json").read_text())
    assert after["api_key"] == "mct_PRESERVED_test", (
        "existing human profile must not be silently overwritten"
    )


def test_init_partial_failure_one_agent_mint_failed_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If ONE platform mint fails, init logs and continues — does not abort."""
    from quorus.cli import _cmd_init

    def _fake_register(*, relay_url: str, parent_api_key: str, suffix: str):
        if suffix == "gemini":
            return None  # simulate relay rejection
        return f"mct_{suffix}_ok"

    monkeypatch.setattr("quorus.cli._register_agent_identity", _fake_register)
    _stub_human_signup(monkeypatch)

    _cmd_init(_make_init_args())  # must NOT raise

    # The other three keys must still land in the persisted config.
    config_path = tmp_path / ".quorus" / "config.json"
    raw = json.loads(config_path.read_text())
    keys: dict[str, str] = raw.get("agent_api_keys", {})
    if not keys:
        # Pointer-style — read profile.
        for slug in raw.get("profiles", []):
            ppath = tmp_path / ".quorus" / "profiles" / f"{slug}.json"
            if ppath.exists():
                pdata = json.loads(ppath.read_text())
                if pdata.get("agent_api_keys"):
                    keys = pdata["agent_api_keys"]
                    break
    # claude + codex + cursor minted; gemini absent.
    assert "arav-claude" in keys
    assert "arav-codex" in keys
    assert "arav-cursor" in keys
    assert "arav-gemini" not in keys


def test_init_help_documents_new_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`quorus init --help` must surface the four new flags."""
    from quorus.cli import main

    monkeypatch.setattr(sys, "argv", ["quorus", "init", "--help"])
    with pytest.raises(SystemExit):
        main()
    out = capsys.readouterr().out
    assert "--no-autostart" in out
    assert "--no-smoke" in out
    assert "--no-launchd" in out
    assert "--auto-launchd" in out
