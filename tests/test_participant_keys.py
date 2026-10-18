"""Participant keys on shared-secret relays: sharing code without Postgres."""

from __future__ import annotations

import hashlib
import hmac
import subprocess
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from quorus import bundle_client
from quorus.auth import tokens
from quorus.auth.participant_keys import (
    ParticipantKeyStore,
    load_or_create_signing_key,
)
from quorus.relay import _reset_state, app

LEGACY = {"Authorization": "Bearer test-secret"}  # conftest's RELAY_SECRET
RELAY = "http://relay.test"


# ── store and signing key ───────────────────────────────────────────────────


def test_first_claim_wins_and_survives_a_restart(tmp_path: Path) -> None:
    store = ParticipantKeyStore(tmp_path)
    key = store.claim("bob")
    assert key and key.startswith("quorus_pk_")
    assert store.claim("bob") is None
    restarted = ParticipantKeyStore(tmp_path)
    assert restarted.name_for(key) == "bob"
    assert restarted.name_for("quorus_pk_guess") is None
    assert (tmp_path / "participant_keys.db").stat().st_mode & 0o777 == 0o600
    assert restarted.release("bob") and restarted.claim("bob") is not None


def test_signing_key_is_random_private_and_stable(tmp_path: Path) -> None:
    key = load_or_create_signing_key(tmp_path)
    assert load_or_create_signing_key(tmp_path) == key
    assert (tmp_path / "jwt-signing.key").stat().st_mode & 0o777 == 0o600
    assert "test-secret" not in key and len(key) >= 48


def test_relay_without_jwt_secret_uses_its_own_key(tmp_path: Path, monkeypatch) -> None:
    from quorus import relay

    monkeypatch.setenv("QUORUS_HISTORY_DB", str(tmp_path / "history.db"))
    monkeypatch.setattr(relay, "DATABASE_URL", "")
    monkeypatch.setattr(tokens, "JWT_SECRET", "")
    relay._ensure_local_jwt_key()
    assert tokens.JWT_SECRET == (tmp_path / "jwt-signing.key").read_text()


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    _reset_state()
    monkeypatch.setattr(tokens, "JWT_SECRET", load_or_create_signing_key(tmp_path))
    app.state.participant_keys = ParticipantKeyStore(tmp_path)
    yield TestClient(app)
    _reset_state()


def _claim(client: TestClient, name: str) -> str:
    resp = client.post("/v1/auth/claim-name", json={"name": name}, headers=LEGACY)
    assert resp.status_code == 200, resp.text
    return resp.json()["participant_key"]


def _jwt(client: TestClient, key: str) -> dict[str, str]:
    resp = client.post("/v1/auth/token", json={"api_key": key})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_claim_is_once_per_name_and_needs_the_shared_secret(client: TestClient) -> None:
    bob = _jwt(client, _claim(client, "bob"))
    again = client.post("/v1/auth/claim-name", json={"name": "bob"}, headers=LEGACY)
    assert again.status_code == 409
    as_bob = client.post("/v1/auth/claim-name", json={"name": "carol"}, headers=bob)
    assert as_bob.status_code == 403
    assert client.post("/v1/auth/token", json={"api_key": "quorus_pk_nope"}).status_code == 401


def test_knowing_the_shared_secret_cannot_mint_a_valid_jwt(client: TestClient) -> None:
    rid = client.post("/rooms", json={"name": "r", "created_by": "alice"},
                      headers=LEGACY).json()["id"]
    client.post(f"/rooms/{rid}/join", json={"participant": "bob"}, headers=LEGACY)
    claims = {"sub": "bob", "tenant_id": "_legacy", "tenant_slug": "_legacy", "role": "user",
              "iss": tokens.JWT_ISSUER, "aud": tokens.JWT_AUDIENCE, "exp": 4102444800}
    derived = hmac.new(b"test-secret", b"quorus-local-jwt", hashlib.sha256).hexdigest()
    for signing_key in ("test-secret", derived):
        forged = jwt.encode(claims, signing_key, algorithm="HS256")
        resp = client.get(f"/v1/rooms/{rid}/bundles",
                          headers={"Authorization": f"Bearer {forged}"})
        assert resp.status_code == 401, signing_key
    real = _jwt(client, _claim(client, "bob"))
    assert client.get(f"/v1/rooms/{rid}/bundles", headers=real).status_code == 200


# ── client: share and fetch on a shared-secret relay, no setup step ─────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def test_shared_secret_users_share_and_fetch_without_api_keys(
        client: TestClient, tmp_path: Path, monkeypatch) -> None:
    class _InProcess:
        post = staticmethod(lambda url, **kw: client.post(url.removeprefix(RELAY), **kw))
        get = staticmethod(lambda url, **kw: client.get(url.removeprefix(RELAY), **kw))

    monkeypatch.setattr(bundle_client, "_http", _InProcess)
    rid = client.post("/rooms", json={"name": "r", "created_by": "alice"},
                      headers=LEGACY).json()["id"]
    client.post(f"/rooms/{rid}/join", json={"participant": "bob"}, headers=LEGACY)

    laptop2 = tmp_path / "laptop2"
    laptop2.mkdir()
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                 ("config", "user.name", "t"), ("commit", "-q", "--allow-empty", "-m", "base"),
                 ("checkout", "-qb", "feat"), ("commit", "-q", "--allow-empty", "-m", "work")):
        _git(laptop2, *args)
    laptop1 = tmp_path / "laptop1"
    _git(tmp_path, "clone", "-q", "--single-branch", "--branch", "main",
         f"file://{laptop2}", str(laptop1))

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "bob-config"))
    bob = bundle_client.bearer(RELAY, secret="test-secret", name="bob")
    assert bundle_client.bearer(RELAY, secret="test-secret", name="bob")  # reuses, no 409
    saved = tmp_path / "bob-config" / "participant_keys.json"
    assert saved.stat().st_mode & 0o777 == 0o600
    meta = bundle_client.upload(RELAY, bob, rid, bundle_client.make_bundle(laptop2)[0])
    assert meta["uploader"] == "bob"

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "alice-config"))
    alice = bundle_client.bearer(RELAY, secret="test-secret", name="alice")
    got = bundle_client.fetch_into(laptop1, *bundle_client.download(RELAY, alice, rid, meta["id"]))
    assert got.ref == "refs/quorus/bob/feat"
    assert _git(laptop1, "rev-parse", got.ref) == _git(laptop2, "rev-parse", "feat")


def test_release_name_command_frees_a_claim(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from quorus_cli.bundle_cmd import cmd_release_name
    from rich.console import Console

    ParticipantKeyStore(tmp_path).claim("bob")
    cmd_release_name(SimpleNamespace(name="bob", data_dir=str(tmp_path)), Console())
    assert ParticipantKeyStore(tmp_path).claim("bob") is not None


def test_release_and_reclaim_retires_the_old_holders_tokens(client: TestClient) -> None:
    """Codex's repro on 98b488c7: after "bob" was released and reclaimed, the
    previous holder's 24h JWT still opened the bundle routes."""
    rid = client.post("/rooms", json={"name": "r", "created_by": "alice"},
                      headers=LEGACY).json()["id"]
    client.post(f"/rooms/{rid}/join", json={"participant": "bob"}, headers=LEGACY)
    url = f"/v1/rooms/{rid}/bundles"
    old = _jwt(client, _claim(client, "bob"))
    assert client.get(url, headers=old).status_code == 200

    store = app.state.participant_keys
    assert store.release("bob")
    assert client.get(url, headers=old).status_code == 401  # released: retired at once

    new = _jwt(client, _claim(client, "bob"))
    assert client.get(url, headers=old).status_code == 401  # reclaimed: old stays dead
    assert client.get(url, headers=new).status_code == 200


def test_claims_file_from_98b488c7_is_migrated(tmp_path: Path) -> None:
    import sqlite3

    with sqlite3.connect(tmp_path / "participant_keys.db") as db:
        db.execute("CREATE TABLE claims (name TEXT PRIMARY KEY, key_hash TEXT NOT NULL, "
                   "created_at REAL NOT NULL)")
        db.execute("INSERT INTO claims VALUES ('bob', 'h', 0)")
    store = ParticipantKeyStore(tmp_path)
    assert not store.is_current("bob", "")  # tokens from before the column are retired
    assert store.claim("bob") is None       # but the claim itself is kept


def test_participant_jwt_without_a_generation_is_refused(client: TestClient) -> None:
    """Codex's repro on 6a93cec1: tokens minted by 98b488c7 carry no pk_claim
    and still got 200 after the name was released and reclaimed."""
    rid = client.post("/rooms", json={"name": "r", "created_by": "alice"},
                      headers=LEGACY).json()["id"]
    client.post(f"/rooms/{rid}/join", json={"participant": "bob"}, headers=LEGACY)
    _claim(client, "bob")
    pre_generation = tokens.create_jwt(sub="bob", tenant_id="_legacy", tenant_slug="_legacy")
    resp = client.get(f"/v1/rooms/{rid}/bundles",
                      headers={"Authorization": f"Bearer {pre_generation}"})
    assert resp.status_code == 401


def test_refused_claims_dont_lock_the_machine_out(client: TestClient) -> None:
    """Release check: a daemon retrying a taken name got ten 409s in 30s, then
    every agent on 127.0.0.1 got 429 for an hour. Only successful claims count."""
    from quorus.auth.participant_routes import CLAIMS_PER_HOUR

    _claim(client, "stuck-codex")
    for _ in range(CLAIMS_PER_HOUR + 5):
        r = client.post("/v1/auth/claim-name", json={"name": "stuck-codex"}, headers=LEGACY)
        assert r.status_code == 409
    for i in range(CLAIMS_PER_HOUR - 1):  # the first claim above used one
        _claim(client, f"agent{i}")
    over = client.post("/v1/auth/claim-name", json={"name": "one-too-many"}, headers=LEGACY)
    assert over.status_code == 429  # the cap still stops a runaway loop
