"""Exercise account-mode agent onboarding against the real relay auth policy."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from quorus_cli import agent_cmd

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_account_owner_can_add_agent_to_room(monkeypatch, tmp_path, harness):
    """No --mode and no --repo: the agent joins. The test owns its HOME and has no
    room binding: it used to pass only on machines whose real ~/.quorus bound #build
    (PR #15 CI, 0.6.4)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(agent_cmd, "_bound_path", lambda room: None)
    _reset_state()
    cfg = {"instance_name": "alice", "relay_url": "http://test", "api_key": "parent-key"}

    def bearer(config, key, legacy):
        return create_jwt(
            sub="alice" if key == "parent-key" else f"alice-{harness}",
            tenant_id="onboarding", tenant_slug="onboarding",
        )

    async def request(method, url, **kwargs):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test",
        ) as client:
            return await client.request(method, url, **kwargs)

    created = asyncio.run(request(
        "POST", "/rooms", json={"name": "build", "created_by": "alice"},
        headers={"Authorization": f"Bearer {bearer(cfg, 'parent-key', False)}"},
    ))
    assert created.status_code == 200
    monkeypatch.setattr(agent_cmd, "load_config", lambda: cfg)
    monkeypatch.setattr(agent_cmd.shutil, "which", lambda _: "/fake/harness")
    monkeypatch.setattr(agent_cmd, "_credentials", lambda *a: ("child-key", False))
    monkeypatch.setattr(agent_cmd, "_bearer", bearer)
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda *a, **k: "already-running")
    monkeypatch.setattr(agent_cmd.httpx, "post", lambda url, **kw: asyncio.run(
        request("POST", url, **kw),
    ))
    args = SimpleNamespace(tool=harness, room="build", repo=None, mode=None)
    console = SimpleNamespace(print=lambda *a, **kw: None)
    try:
        agent_cmd._add(args, console)
        members = asyncio.run(app.state.room_service.get_members(
            "onboarding", created.json()["id"],
        ))
        assert f"alice-{harness}" in members
    finally:
        _reset_state()


def test_mode_without_repo_or_binding_says_what_is_missing(monkeypatch, tmp_path):
    """--mode lives on the room's workspace binding (bug 9): with no --repo and no
    binding on this machine, `agent add` stops with a clear message before anything
    is claimed or joined."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(agent_cmd, "_bound_path", lambda room, state_dir=None: None)
    monkeypatch.setattr(agent_cmd, "load_config", lambda: {
        "instance_name": "alice", "relay_url": "http://test", "api_key": "parent-key"})
    monkeypatch.setattr(agent_cmd.shutil, "which", lambda _: "/fake/harness")
    monkeypatch.setattr(agent_cmd, "_readiness_warnings", lambda *a: [])

    def no_network(*a, **kw):
        raise AssertionError("agent add reached the relay before refusing")

    monkeypatch.setattr(agent_cmd.httpx, "post", no_network)
    monkeypatch.setattr(agent_cmd, "_credentials", no_network)
    args = SimpleNamespace(tool="claude", room="build", repo=None, mode="default")
    with pytest.raises(SystemExit) as exc:
        agent_cmd._add(args, SimpleNamespace(print=lambda *a, **kw: None))
    assert "--mode applies to the room's workspace" in str(exc.value)
    assert "#build has none on this machine yet" in str(exc.value)
