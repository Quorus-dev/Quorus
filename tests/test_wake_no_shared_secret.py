"""A woken agent never holds the relay's shared secret.

The secret is an admin credential: with it an agent can post as anyone,
claim any free name, and (before the approvals fix) approve its own tool
calls as its owner. On a shared-secret relay the daemon signs in with the
agent's own participant key, so the agent gets that key and acts only as
itself.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)

from quorus import bundle_client  # noqa: E402

SECRET = "relay-shared-secret-value"
KEY = "quorus_pk_agentsownkey0123456789"
RELAY = "http://relay.test"


def _daemon(tmp_path: Path, monkeypatch, participant: str = "arav-claude") -> "reflexd.Reflexd":
    monkeypatch.setenv("RELAY_SECRET", SECRET)  # the daemon's own environment
    monkeypatch.setenv("API_KEY", SECRET)
    cfg = reflexd.ReflexdConfig(relay_url=RELAY, api_key=SECRET, legacy_bearer=True,
                                participant_name=participant, runtime_dir=tmp_path,
                                spawn_enabled=False)
    d = reflexd.Reflexd(cfg)
    # What the daemon's sign-in saved (RelayClient key_dir is the agent dir).
    bundle_client.save_participant_key(RELAY, participant, KEY,
                                       tmp_path / "agents" / participant)
    d._refresh_identity()
    return d


def _everything_the_agent_sees(d: "reflexd.Reflexd") -> str:
    agent_dir = d.adapter.agent_config_dir
    spec = d.adapter.wake_spec
    files = "".join(p.read_text() for p in agent_dir.rglob("*.json")
                    if p.name != "participant_keys.json")
    return "\n".join([
        files,
        json.dumps(reflexd.reflexd_wake.wake_env(spec, agent_dir)),
        json.dumps(reflexd.reflexd_models.gemini_settings(spec)),
        json.dumps(reflexd.reflexd_models.opencode_config(spec, mode="autonomous", model=None)),
        " ".join(reflexd.reflexd_wake.codex_wake_flags(spec)),
    ])


def test_woken_agent_gets_its_own_key_not_the_secret(tmp_path, monkeypatch) -> None:
    d = _daemon(tmp_path, monkeypatch)
    leaked = SECRET in _everything_the_agent_sees(d)  # a bool: never print the env
    assert not leaked
    profile = json.loads((d.adapter.agent_config_dir / "profiles" / "arav-claude.json")
                         .read_text())
    assert profile["api_key"] == KEY and "relay_secret" not in profile
    assert d.adapter.wake_spec["env"]["QUORUS_API_KEY"] == KEY
    assert "QUORUS_RELAY_SECRET" not in d.adapter.wake_spec["env"]


def test_cursor_and_cline_do_not_inherit_the_daemons_secret(tmp_path, monkeypatch) -> None:
    for harness, participant in (("cline", "arav-cline"), ("cursor", "arav-cursor")):
        d = _daemon(tmp_path / harness, monkeypatch, participant)
        seen: dict = {}

        async def fake_subprocess(argv, *, parser, cwd=None, timeout_s=None, env=None):
            seen["env"] = env
            return "ok"

        monkeypatch.setattr(d.adapter, "_run_subprocess", fake_subprocess)
        monkeypatch.setattr(reflexd.shutil, "which", lambda b: f"/usr/bin/{b}")
        asyncio.run(d.adapter.run(harness, context="hi"))
        assert seen["env"] is not None, harness
        leaked = SECRET in json.dumps(seen["env"])
        assert not leaked, harness
        assert seen["env"]["QUORUS_CONFIG_DIR"] == str(d.adapter.agent_config_dir)


def _new_agent(tmp_path, monkeypatch) -> "reflexd.Reflexd":
    """A daemon for an agent that has never signed in: no saved key yet."""
    monkeypatch.setenv("RELAY_SECRET", SECRET)
    cfg = reflexd.ReflexdConfig(relay_url=RELAY, api_key=SECRET, legacy_bearer=True,
                                participant_name="new-claude", runtime_dir=tmp_path,
                                spawn_enabled=False)
    return reflexd.Reflexd(cfg)


def _written(tmp_path) -> str:
    return "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())


def test_new_agent_gets_no_secret_and_no_warning_before_sign_in(tmp_path, monkeypatch,
                                                                caplog) -> None:
    caplog.set_level("WARNING")
    d = _new_agent(tmp_path, monkeypatch)
    assert SECRET not in _written(tmp_path)  # a bool: never print the value
    assert d.adapter.wake_spec is None
    assert "has no participant keys" not in caplog.text


def test_after_sign_in_the_agent_gets_its_own_key(tmp_path, monkeypatch) -> None:
    d = _new_agent(tmp_path, monkeypatch)
    d._relay = type("R", (), {"keys_supported": True})()
    bundle_client.save_participant_key(RELAY, "new-claude", KEY,
                                       tmp_path / "agents" / "new-claude")
    d._refresh_identity()
    assert SECRET not in _written(tmp_path)
    assert d.adapter.wake_spec["env"]["QUORUS_API_KEY"] == KEY


def test_keys_supported_but_none_saved_still_writes_no_secret(tmp_path, monkeypatch) -> None:
    d = _new_agent(tmp_path, monkeypatch)
    d._relay = type("R", (), {"keys_supported": True})()
    d._refresh_identity()
    assert SECRET not in _written(tmp_path) and d.adapter.wake_spec is None


def test_only_a_relay_without_keys_gives_the_secret_and_says_so_once(tmp_path, monkeypatch,
                                                                     caplog) -> None:
    caplog.set_level("WARNING")
    d = _new_agent(tmp_path, monkeypatch)
    d._relay = type("R", (), {"keys_supported": False})()
    d._refresh_identity()
    d._refresh_identity()
    assert d.adapter.wake_spec["env"].get("QUORUS_RELAY_SECRET") == SECRET
    assert caplog.text.count("has no participant keys") == 1
