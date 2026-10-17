"""The wake daemon signs in with its own participant key on shared-secret relays."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from quorus.auth import tokens
from quorus.auth.participant_keys import ParticipantKeyStore, load_or_create_signing_key
from quorus.relay import _reset_state, app

_spec = importlib.util.spec_from_file_location(
    "reflexd", Path(__file__).resolve().parent.parent / "scripts" / "reflexd.py")
reflexd = sys.modules.get("reflexd") or importlib.util.module_from_spec(_spec)
if "reflexd" not in sys.modules:
    sys.modules["reflexd"] = reflexd
    _spec.loader.exec_module(reflexd)

SECRET = "test-secret"
LEGACY = {"Authorization": f"Bearer {SECRET}"}


def _client(relay_url: str, key_dir: Path):
    rc = reflexd.RelayClient(relay_url=relay_url, api_key=SECRET, legacy_bearer=True,
                             participant="a-codex", key_dir=key_dir)
    rc._client = AsyncClient(transport=ASGITransport(app=app), base_url=relay_url)
    return rc


def test_daemon_social_verbs_work_on_a_shared_secret_relay(tmp_path, monkeypatch) -> None:
    """Live failover proof: POST /v1/social/defer got 403 for legacy auth, so on
    the default relay failover could never hand off."""
    _reset_state()
    monkeypatch.setattr(tokens, "JWT_SECRET", load_or_create_signing_key(tmp_path / "relay"))
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")

    async def go() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://r") as c:
            rid = (await c.post("/rooms", json={"name": "r", "created_by": "a-codex"},
                                headers=LEGACY)).json()["id"]
            for who in ("a-codex", "b-claude"):
                await c.post(f"/rooms/{rid}/join", json={"participant": who}, headers=LEGACY)
            legacy = await c.post("/v1/social/defer", headers=LEGACY, json={
                "actor": "a-codex", "room_id": rid, "payload": {"to": "b-claude"}})
            assert legacy.status_code == 403  # the failure the live proof hit

        relay = _client("http://r", tmp_path / "agent")
        token = await relay.bearer_jwt()
        assert token != SECRET and token.count(".") == 2
        await relay.post_social_defer(room_id=rid, actor="a-codex", target="b-claude")

        again = _client("http://r", tmp_path / "agent")  # restart: reuses the key, no 409
        assert (await again.bearer_jwt()).count(".") == 2

    asyncio.run(go())
    assert (tmp_path / "agent" / "participant_keys.json").stat().st_mode & 0o777 == 0o600
    _reset_state()


def test_relay_without_claims_keeps_the_shared_secret(tmp_path, monkeypatch) -> None:
    _reset_state()
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")  # claim-name answers 404

    async def go() -> str:
        return await _client("http://r", tmp_path / "agent").bearer_jwt()

    assert asyncio.run(go()) == SECRET
    _reset_state()
