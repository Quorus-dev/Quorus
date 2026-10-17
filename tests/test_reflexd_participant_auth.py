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


def _relay_with_room(tmp_path, monkeypatch):
    _reset_state()
    monkeypatch.setattr(tokens, "JWT_SECRET", load_or_create_signing_key(tmp_path / "relay"))
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")

    async def make_room() -> str:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://r") as c:
            rid = (await c.post("/rooms", json={"name": "r", "created_by": "a-codex"},
                                headers=LEGACY)).json()["id"]
            for who in ("a-codex", "b-claude"):
                await c.post(f"/rooms/{rid}/join", json={"participant": who}, headers=LEGACY)
            return rid

    return asyncio.run(make_room())


def test_concurrent_startup_calls_claim_once_and_never_fall_back(tmp_path, monkeypatch,
                                                                 caplog) -> None:
    """Overseer's live log: 'claim-name 409 Conflict … using the shared secret' at
    startup. Heartbeat and SSE both signed in at once; the loser cached the secret."""
    _relay_with_room(tmp_path, monkeypatch)

    async def go() -> list[str]:
        relay = _client("http://r", tmp_path / "agent")
        return await asyncio.gather(*(relay.bearer_jwt() for _ in range(5)))

    tokens_seen = asyncio.run(go())
    assert all(t != SECRET and t.count(".") == 2 for t in tokens_seen)
    assert "shared secret" not in caplog.text
    _reset_state()


def test_restart_reuses_the_saved_key_without_claiming(tmp_path, monkeypatch, caplog) -> None:
    rid = _relay_with_room(tmp_path, monkeypatch)

    async def first() -> None:
        relay = _client("http://r", tmp_path / "agent")
        await relay.bearer_jwt()

    async def after_restart() -> None:
        relay = _client("http://r", tmp_path / "agent")
        calls: list[str] = []
        orig = relay._client.post

        async def spy(url, *a, **kw):
            calls.append(url)
            return await orig(url, *a, **kw)

        relay._client.post = spy
        await relay.post_social_defer(room_id=rid, actor="a-codex", target="b-claude")
        assert "/v1/auth/claim-name" not in calls  # saved key, no new claim

    asyncio.run(first())
    asyncio.run(after_restart())
    assert "409" not in caplog.text and "shared secret" not in caplog.text
    _reset_state()


def test_name_claimed_elsewhere_fails_closed(tmp_path, monkeypatch) -> None:
    """Codex on 397dd2db: a 409 with no saved key must not fall back to the
    shared admin secret."""
    import pytest

    _relay_with_room(tmp_path, monkeypatch)
    assert app.state.participant_keys.claim("a-codex")  # someone else holds the name

    async def go() -> None:
        await _client("http://r", tmp_path / "agent").bearer_jwt()

    with pytest.raises(reflexd.ParticipantAuthError, match="release-name"):
        asyncio.run(go())
    _reset_state()


def test_rejected_saved_key_fails_closed(tmp_path, monkeypatch) -> None:
    import pytest

    from quorus import bundle_client

    _relay_with_room(tmp_path, monkeypatch)
    bundle_client.save_participant_key("http://r", "a-codex", "quorus_pk_stale",
                                       tmp_path / "agent")

    async def go() -> None:
        await _client("http://r", tmp_path / "agent").bearer_jwt()

    with pytest.raises(reflexd.ParticipantAuthError, match="rejected"):
        asyncio.run(go())
    _reset_state()


def test_fail_closed_error_is_a_relay_error_the_loops_retry() -> None:
    import httpx

    assert issubclass(reflexd.ParticipantAuthError, httpx.HTTPError)


def _redirecting_transport(seen: list[str]):
    """A relay that 307s every credential exchange to another host (codex's
    repro on dc24ee6c): a client that follows resends the key in the body."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "evil":
            return httpx.Response(200, json={"token": "x.y.z", "participant_key": "k"})
        return httpx.Response(307, headers={"Location": f"http://evil{request.url.path}"})

    return httpx.MockTransport(handler)


def test_credential_exchanges_never_follow_a_redirect(tmp_path) -> None:
    import pytest

    from quorus import bundle_client

    async def sign_in(*, legacy: bool, saved_key: bool) -> None:
        if saved_key:
            bundle_client.save_participant_key("http://r", "a-codex", "quorus_pk_mine",
                                               tmp_path / "agent")
        rc = reflexd.RelayClient(relay_url="http://r", api_key=SECRET, legacy_bearer=legacy,
                                 participant="a-codex", key_dir=tmp_path / "agent")
        # The daemon's client follows redirects for everything else.
        rc._client = AsyncClient(transport=_redirecting_transport(seen), base_url="http://r",
                                 follow_redirects=True)
        await rc.bearer_jwt()

    for legacy, saved_key in ((True, False), (True, True), (False, False)):
        seen: list[str] = []
        with pytest.raises(Exception) as err:
            asyncio.run(sign_in(legacy=legacy, saved_key=saved_key))
        assert isinstance(err.value, reflexd.httpx.HTTPError), err.value
        assert seen and not any(s.startswith("evil") for s in seen), seen


def test_cli_credential_exchange_never_follows_a_redirect(tmp_path, monkeypatch) -> None:
    import httpx
    import pytest

    from quorus import bundle_client

    seen: list[str] = []
    monkeypatch.setattr(bundle_client, "_http", httpx.Client(
        transport=_redirecting_transport(seen), follow_redirects=True))
    for kw in ({"api_key": "quorus_ak_x"}, {"secret": SECRET, "name": "a-codex"}):
        with pytest.raises(bundle_client.BundleClientError, match="redirected"):
            bundle_client.bearer("http://r", store_dir=tmp_path, **kw)
    assert seen and not any(s.startswith("evil") for s in seen), seen


def test_a_taken_name_is_not_retried_on_every_reconnect(tmp_path, monkeypatch) -> None:
    """Release check: reflexd re-claimed on every stream (~30s) and its 409s
    locked the machine out. Now: one claim, one owner alert, then a 10-minute
    pause, unless the key turns up meanwhile."""
    import pytest

    from quorus import bundle_client

    _relay_with_room(tmp_path, monkeypatch)
    real_key = app.state.participant_keys.claim("a-codex")  # claimed from elsewhere
    told: list[str] = []
    calls: list[str] = []
    clock = [1_000_000.0]
    monkeypatch.setattr(reflexd.time, "time", lambda: clock[0])

    async def go() -> str:
        relay = _client("http://r", tmp_path / "agent")
        relay.on_name_taken = told.append
        orig = relay._client.post

        async def spy(url, *a, **kw):
            calls.append(url)
            return await orig(url, *a, **kw)

        relay._client.post = spy
        for _ in range(5):  # five reconnects in a row
            with pytest.raises(reflexd.ParticipantAuthError, match="release-name"):
                await relay._participant_jwt()
        assert calls.count("/v1/auth/claim-name") == 1 and len(told) == 1
        clock[0] += reflexd.CLAIM_BACKOFF_S + 1
        with pytest.raises(reflexd.ParticipantAuthError):
            await relay._participant_jwt()
        assert calls.count("/v1/auth/claim-name") == 2 and len(told) == 1  # asked again, quietly
        # the owner copies the key over: it's used at once, backoff or not
        bundle_client.save_participant_key("http://r", "a-codex", real_key, tmp_path / "agent")
        return await relay._participant_jwt()

    assert asyncio.run(go()).count(".") == 2
    _reset_state()
