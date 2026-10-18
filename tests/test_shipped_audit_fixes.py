"""Regression tests for the v0.5.0 shipped-package security audit (S1-S5)."""

import pytest
from fastapi.testclient import TestClient

from quorus.auth import tokens
from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path)
    monkeypatch.setattr(
        tokens, "JWT_SECRET", "audit-dummy-signing-value-at-least-32-bytes"
    )
    yield TestClient(app)
    _reset_state()


def _identity(client, name):
    key = client.post(
        "/v1/auth/claim-name", headers=ADMIN, json={"name": name}
    ).json()["participant_key"]
    token = client.post("/v1/auth/token", json={"api_key": key}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _room_with_lock(client):
    alice, bob = _identity(client, "alice"), _identity(client, "bob")
    rid = client.post(
        "/rooms",
        headers=alice,
        json={"name": "private", "created_by": "alice", "private": True},
    ).json()["id"]
    assert client.post(
        f"/rooms/{rid}/join", headers=alice, json={"participant": "bob"}
    ).status_code == 200
    lock = client.post(
        f"/rooms/{rid}/lock",
        headers=alice,
        json={"file_path": "app.py", "claimed_by": "alice"},
    )
    assert lock.status_code == 200
    return rid, alice, bob, lock.json()["lock_token"]


# S1 ------------------------------------------------------------------------


def test_room_state_hides_lock_token_from_peers(client):
    rid, alice, bob, token = _room_with_lock(client)
    state = client.get(f"/rooms/{rid}/state", headers=bob).json()
    assert "lock_token" not in state["locked_files"]["app.py"]
    assert all("lock_token" not in t for t in state["claimed_tasks"])
    assert token not in str(state)
    # The holder still sees its own token.
    own = client.get(f"/rooms/{rid}/state", headers=alice).json()
    assert own["locked_files"]["app.py"]["lock_token"] == token


def test_peer_cannot_release_another_members_lock(client):
    rid, alice, bob, token = _room_with_lock(client)
    released = client.request(
        "DELETE", f"/rooms/{rid}/lock/app.py", headers=bob,
        json={"lock_token": token},
    )
    assert released.status_code == 403
    still = client.get(f"/rooms/{rid}/state", headers=alice).json()
    assert "app.py" in still["locked_files"]
    ok = client.request(
        "DELETE", f"/rooms/{rid}/lock/app.py", headers=alice,
        json={"lock_token": token},
    )
    assert ok.status_code == 200


# S2 ------------------------------------------------------------------------


def test_direct_clients_cannot_rotate_forwarded_ips_to_bypass_limits(
    client, monkeypatch
):
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    for i in range(100):
        r = client.get(
            "/v1/join/resolve/ZZZZ-ZZZZ",
            headers={"X-Forwarded-For": f"198.51.100.{i}"},
        )
        assert r.status_code in (404, 429)
    r = client.get(
        "/v1/join/resolve/ZZZZ-ZZZZ", headers={"X-Forwarded-For": "203.0.113.1"}
    )
    assert r.status_code == 429


def test_forwarded_ip_used_only_behind_a_configured_proxy(monkeypatch):
    from types import SimpleNamespace

    from quorus.config import get_real_ip

    req = SimpleNamespace(
        headers={"X-Forwarded-For": "203.0.113.9"},
        client=SimpleNamespace(host="10.0.0.5"),
    )
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    assert get_real_ip(req) == "10.0.0.5"
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    assert get_real_ip(req) == "203.0.113.9"
