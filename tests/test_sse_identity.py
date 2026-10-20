"""Published-0.6.3 audit: SSE must not inherit a released recipient's identity."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app
from quorus.routes.sse import stream_messages
from quorus.services import sse_identity
from tests.test_member_agents import ADMIN, _agent, _join, _say


@pytest.fixture(name="client")
async def _client(tmp_path):
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for room in ("build", "ops"):
            r = await c.post("/rooms", json={"name": room, "created_by": "alice"}, headers=ADMIN)
            assert r.status_code == 200
        yield c
    _reset_state()


async def _token(c, auth, name="bob"):
    response = await c.post("/stream/token", json={"recipient": name}, headers=auth)
    assert response.status_code == 200
    return response.json()["token"]


def _request():
    return Request({"type": "http", "app": app, "headers": []})


async def _opened(c, auth, name="bob"):
    token = await _token(c, auth, name)
    response = await stream_messages(name, _request(), token)
    iterator = response.body_iterator
    assert "event: connected" in await anext(iterator)
    return iterator


async def test_preissued_token_cannot_reconnect_after_release_and_reclaim(client):
    _, bob = await _join(client, "build", "bob")
    token = await _token(client, bob)
    store = app.state.participant_keys
    assert store.release("bob")
    assert store.claim("bob", kind="human")
    with pytest.raises(HTTPException) as exc:
        await stream_messages("bob", _request(), token)
    assert exc.value.status_code == 401


async def test_open_stream_never_reads_successor_traffic(client):
    _, bob = await _join(client, "build", "bob")
    iterator = await _opened(client, bob)
    store = app.state.participant_keys
    assert store.release("bob")
    assert store.claim("bob", kind="human")
    assert await _say(client, ADMIN, "alice") == 200
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert not app.state.sse_service._queues["_legacy:bob"]


async def test_agent_stream_closes_when_parent_claim_is_released(client):
    _, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    iterator = await _opened(client, agent, "bob-codex")
    assert app.state.participant_keys.release("bob")
    app.state.sse_service.push("_legacy", "bob-codex", {"content": "private canary"})
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


@pytest.mark.parametrize("action", ["kick", "revoke"])
async def test_queued_room_event_is_dropped_after_membership_removal(client, action):
    inv, bob = await _join(client, "build", "bob")
    iterator = await _opened(client, bob)
    assert await _say(client, ADMIN, "alice") == 200  # queue an event before removal
    if action == "kick":
        r = await client.post("/rooms/build/kick", headers=ADMIN,
                              json={"participant": "bob", "requested_by": "alice"})
    else:
        r = await client.post(f"/v1/join/invites/{inv}/revoke", headers=ADMIN)
    assert r.status_code == 200
    # Losing one room does not revoke relay-level DM access. Its event lets us
    # prove that the stale queued room event was skipped without waiting 30 s.
    app.state.sse_service.push("_legacy", "bob", {"content": "own-direct-message"})
    frame = await anext(iterator)
    assert "own-direct-message" in frame and '"room"' not in frame
    await iterator.aclose()


async def test_plain_preupgrade_token_cannot_open_a_claimed_name(client):
    await _join(client, "build", "bob")
    token = await app.state.sse_service.create_token("_legacy", "bob", 300)
    with pytest.raises(HTTPException) as exc:
        await stream_messages("bob", _request(), token)
    assert exc.value.status_code == 401


async def test_changing_wrapper_claim_does_not_change_backend_authority(client):
    _, bob = await _join(client, "build", "bob")
    _, carol = await _join(client, "build", "carol")
    token = await _token(client, bob)
    other = await _token(client, carol, "carol")
    claim, _ = sse_identity.unwrap(other)
    _, opaque = sse_identity.unwrap(token)
    with pytest.raises(HTTPException) as exc:
        await stream_messages("bob", _request(), sse_identity.wrap(opaque, claim))
    assert exc.value.status_code == 401


async def test_operator_minted_stream_for_claimed_name_is_bound_too(client):
    await _join(client, "build", "bob")
    token = await _token(client, ADMIN)
    assert token.startswith(sse_identity.PREFIX)
    response = await stream_messages("bob", _request(), token)
    await anext(response.body_iterator)
    await response.body_iterator.aclose()


async def test_unclaimed_operator_token_does_not_inherit_a_later_claim(client):
    token = await _token(client, ADMIN, "fresh")
    assert app.state.participant_keys.claim("fresh")
    with pytest.raises(HTTPException) as exc:
        await stream_messages("fresh", _request(), token)
    assert exc.value.status_code == 401


# ── the agent-DM stream checks the same generation (live replay on f9984702) ──


async def _dm_opened(c, auth, name="bob"):
    from quorus.routes.agent_dm import stream_agent_dms

    response = await stream_agent_dms(name, _request(), await _token(c, auth, name))
    iterator = response.body_iterator
    assert "event: connected" in await anext(iterator)
    return iterator


async def test_a_key_bound_token_opens_both_streams(client):
    """Every agent's /stream/dm was 401 'Invalid token' while /stream/<name> took the
    same kind of token: only the room route unwrapped the generation-bound token."""
    _, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    await _opened(client, agent, "bob-codex")
    await _dm_opened(client, agent, "bob-codex")


async def test_bound_tokens_open_both_the_room_and_the_dm_stream(client):
    """Live replay of 4e036f88: /stream/token mints identity-bound tokens, but the DM
    stream still verified them bare, so every agent's DM stream answered 401 and owner
    alerts and tuning notes never arrived live."""
    from quorus.routes.agent_dm import stream_agent_dms

    _, bob = await _join(client, "build", "bob")
    room = await stream_messages("bob", _request(), await _token(client, bob))
    assert "event: connected" in await anext(room.body_iterator)
    dm = await stream_agent_dms("bob", _request(), await _token(client, bob))
    assert "event: connected" in await anext(dm.body_iterator)


async def test_a_dm_token_is_refused_after_release_and_reclaim(client):
    from quorus.routes.agent_dm import stream_agent_dms

    _, bob = await _join(client, "build", "bob")
    token = await _token(client, bob)
    store = app.state.participant_keys
    assert store.release("bob")
    assert store.claim("bob", kind="human")
    with pytest.raises(HTTPException) as exc:
        await stream_agent_dms("bob", _request(), token)
    assert exc.value.status_code == 401


async def test_an_open_dm_stream_closes_when_the_name_is_released(client, monkeypatch):
    from quorus.routes import agent_dm

    _, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    iterator = await _dm_opened(client, agent, "bob-codex")
    assert app.state.participant_keys.release("bob")  # the agent's parent claim
    for q in agent_dm._get_dm_queues(_request()).get(("_legacy", "bob-codex"), ()):
        q.put_nowait({"content": "private canary"})
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


async def test_dm_replay_never_reaches_a_released_streams_old_holder(client):
    """Codex on e0f6e3ac: open through `connected`, release and reclaim bob, then a
    DM to the new bob lands in the inbox; the old stream's replay must not yield it."""
    from quorus.routes.agent_dm import stream_agent_dms

    _, bob = await _join(client, "build", "bob")
    _, carol = await _join(client, "build", "carol")
    dm = await stream_agent_dms("bob", _request(), await _token(client, bob))
    iterator = dm.body_iterator
    assert "event: connected" in await anext(iterator)
    store = app.state.participant_keys
    assert store.release("bob")
    assert store.claim("bob", kind="human")
    sent = await client.post("/v1/dm", json={"from": "carol", "to": "bob",
                                              "content": "successor-only canary"},
                             headers=carol)
    assert sent.status_code == 200, sent.text
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
