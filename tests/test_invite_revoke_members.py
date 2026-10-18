"""Revoking an invite cuts off the people who joined with it, on their next request.

Their role lives on the room's membership row, which the relay reads on every
request (room_auth.require_room_member), so removing that row is the revoke: no
token to expire, no cache to wait out (0.6.0, secret-free invites).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}


@pytest.fixture
async def client(tmp_path):
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for room in ("build", "ops"):
            r = await c.post("/rooms", json={"name": room, "created_by": "arav"}, headers=ADMIN)
            assert r.status_code == 200, r.text
        yield c
    _reset_state()


async def _join(c: AsyncClient, room: str, name: str, **mint) -> tuple[str, dict]:
    """Mint an invite to *room*, redeem it as *name*; return (invite id, auth header)."""
    r = await c.post("/v1/join/mint", json={"room": room, **mint}, headers=ADMIN)
    assert r.status_code == 200, r.text
    inv_id = r.json()["invite_id"]
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    key = (await c.post("/v1/join/redeem", json={"code": payload["c"], "name": name})
           ).json()["participant_key"]
    jwt = (await c.post("/v1/auth/token", json={"api_key": key})).json()["token"]
    return inv_id, {"Authorization": f"Bearer {jwt}"}


async def test_the_role_is_on_the_membership_row(client: AsyncClient) -> None:
    await _join(client, "build", "bob")
    room = (await client.get("/rooms/build", headers=ADMIN)).json()
    assert "bob" in room["members"] and room["member_roles"]["bob"] == "member"


async def test_revoking_an_invite_cuts_off_who_joined_with_it(client: AsyncClient) -> None:
    inv_id, bob = await _join(client, "build", "bob")
    assert (await client.get("/rooms/build/history", headers=bob)).status_code == 200

    r = await client.post(f"/v1/join/invites/{inv_id}/revoke", headers=ADMIN)
    assert r.status_code == 200 and r.json()["removed"] == ["bob"]

    assert (await client.get("/rooms/build/history", headers=bob)).status_code == 403
    sent = await client.post("/rooms/build/messages",
                             json={"from_name": "bob", "content": "still here?"}, headers=bob)
    assert sent.status_code == 403
    rejoin = await client.post("/rooms/build/join", json={"participant": "bob"}, headers=bob)
    assert rejoin.status_code == 403


async def test_revoke_spares_people_who_joined_with_other_invites(client) -> None:
    inv_bob, bob = await _join(client, "build", "bob")
    _, carol = await _join(client, "build", "carol")
    _, dan = await _join(client, "ops", "dan")

    await client.post(f"/v1/join/invites/{inv_bob}/revoke", headers=ADMIN)

    assert (await client.get("/rooms/build/history", headers=carol)).status_code == 200
    assert (await client.get("/rooms/ops/history", headers=dan)).status_code == 200


async def test_a_kicked_invitee_is_out_on_the_next_request(client: AsyncClient) -> None:
    _, bob = await _join(client, "build", "bob")
    r = await client.post("/rooms/build/kick", json={"participant": "bob", "requested_by": "arav"},
                          headers=ADMIN)
    assert r.status_code == 200, r.text
    assert (await client.get("/rooms/build/history", headers=bob)).status_code == 403


async def test_a_revoke_landing_mid_redeem_still_keeps_them_out(client: AsyncClient) -> None:
    """Codex's race on 79d70198: the redeem has spent the use but not yet joined the
    room when the owner revokes, so revoke finds no member to remove; then the join
    lands. The redeem must check the invite again after joining."""
    r = await client.post("/v1/join/mint", json={"room": "build"}, headers=ADMIN)
    inv_id = r.json()["invite_id"]
    payload = (await client.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]

    rooms = app.state.room_service
    real_join = rooms.join

    async def join_after_a_revoke(*args, **kwargs):
        revoked = await client.post(f"/v1/join/invites/{inv_id}/revoke", headers=ADMIN)
        assert revoked.status_code == 200 and revoked.json()["removed"] == []
        return await real_join(*args, **kwargs)

    rooms.join = join_after_a_revoke
    try:
        redeem = await client.post("/v1/join/redeem",
                                   json={"code": payload["c"], "name": "bob"})
    finally:
        rooms.join = real_join
    assert redeem.status_code == 403
    assert "participant_key" not in redeem.text
    room = (await client.get("/rooms/build", headers=ADMIN)).json()
    assert "bob" not in room["members"]
    assert app.state.participant_keys.info("bob") is None
