"""`quorus approve` and the TUI's /approve sign in as the human who decides.

The relay no longer believes a shared-secret caller about who it is
(test_approvals.py), so both clients trade the secret for that human's own
participant JWT first. These run each client against the real relay app.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quorus import bundle_client
from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

RELAY = "http://relay.test"
LEGACY = {"Authorization": "Bearer test-secret"}  # conftest's RELAY_SECRET


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "laptop"))
    tc = TestClient(app)

    class _InProcess:
        post = staticmethod(lambda url, **kw: tc.post(url.removeprefix(RELAY), **kw))
        get = staticmethod(lambda url, **kw: tc.get(url.removeprefix(RELAY), **kw))

    monkeypatch.setattr(bundle_client, "_http", _InProcess)
    yield tc
    _reset_state()


def _pending(client: TestClient) -> str:
    rid = client.post("/rooms", json={"name": "apr", "created_by": "arav"},
                      headers=LEGACY).json()["id"]
    for who in ("arav-claude", "arav"):
        client.post(f"/rooms/{rid}/join", json={"participant": who}, headers=LEGACY)
    rec = client.post("/v1/approvals", headers=LEGACY, json={
        "room_id": rid, "agent": "arav-claude", "tool_name": "Bash",
        "tool_input": "rm -rf build/"}).json()
    return rec["id"]


def _status(client: TestClient, approval_id: str) -> dict:
    return client.get(f"/v1/approvals/{approval_id}", headers=LEGACY).json()


def _cli(monkeypatch, client: TestClient, name: str):
    from quorus_cli import cli

    class _Async:
        async def post(self, url, **kw):
            kw.pop("timeout", None)
            return client.post(url.removeprefix(RELAY), **kw)

    monkeypatch.setattr(cli, "RELAY_URL", RELAY)
    monkeypatch.setattr(cli, "RELAY_SECRET", "test-secret")
    monkeypatch.setattr(cli, "API_KEY", "")
    monkeypatch.setattr(cli, "INSTANCE_NAME", name)
    monkeypatch.setattr(cli, "_get_client", lambda: _Async())
    return cli


async def test_cli_approve_signs_in_as_the_owner(client, monkeypatch) -> None:
    approval_id = _pending(client)
    cli = _cli(monkeypatch, client, "arav")
    await cli._approval_decide(approval_id, True, "")
    rec = _status(client, approval_id)
    assert (rec["status"], rec["decided_by"]) == ("approved", "arav")


async def test_cli_approve_from_an_agent_profile_is_refused(client, monkeypatch) -> None:
    approval_id = _pending(client)
    cli = _cli(monkeypatch, client, "arav-claude")
    with pytest.raises(SystemExit) as exc:
        await cli._approval_decide(approval_id, True, "")
    assert exc.value.code == 3
    assert _status(client, approval_id)["status"] == "pending"


def _tui(monkeypatch, client: TestClient, name: str):
    from quorus_tui import hub

    class _Sync:
        @staticmethod
        def post(url, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            return client.post(url.removeprefix(RELAY), **kw)

    monkeypatch.setattr(hub, "httpx", _Sync)
    monkeypatch.setattr(hub, "_instance_name_for_approvals", lambda: name)
    return hub


def test_tui_approve_signs_in_as_the_owner(client, monkeypatch) -> None:
    approval_id = _pending(client)
    hub = _tui(monkeypatch, client, "arav")
    ok, msg = hub._decide_approval(RELAY, "test-secret", approval_id, True)
    assert ok, msg
    rec = _status(client, approval_id)
    assert (rec["status"], rec["decided_by"]) == ("approved", "arav")


def test_tui_approve_as_an_agent_is_refused(client, monkeypatch) -> None:
    approval_id = _pending(client)
    hub = _tui(monkeypatch, client, "arav-claude")
    ok, msg = hub._decide_approval(RELAY, "test-secret", approval_id, True)
    assert not ok and "agent" in msg
    assert _status(client, approval_id)["status"] == "pending"
