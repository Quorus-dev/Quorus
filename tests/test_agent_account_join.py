"""Exercise account-mode agent onboarding against the real relay auth policy."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from quorus_cli import agent_cmd

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_account_owner_can_add_agent_to_room(monkeypatch, harness):
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
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda _: "already-running")
    monkeypatch.setattr(agent_cmd.httpx, "post", lambda url, **kw: asyncio.run(
        request("POST", url, **kw),
    ))
    args = SimpleNamespace(tool=harness, room="build", repo=None, mode="default")
    console = SimpleNamespace(print=lambda *a, **kw: None)
    try:
        agent_cmd._add(args, console)
        members = asyncio.run(app.state.room_service.get_members(
            "onboarding", created.json()["id"],
        ))
        assert f"alice-{harness}" in members
    finally:
        _reset_state()
