"""Invited members bring their own agents (docs/internal/bets/invitee-agents.md).

bob joined alice's shared-secret relay with an invite. His own key claims
`bob-<tool>` as an agent he owns, which he adds to rooms he may write in. The
agent acts only while bob is in the room, never above bob's role, and dies with
bob's claim (invite-roles.md, "Agents belong to a person").
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth import member_agents
from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}


@pytest.fixture
async def client(tmp_path):
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(tmp_path / "relay")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for room in ("build", "ops"):
            r = await c.post("/rooms", json={"name": room, "created_by": "alice"}, headers=ADMIN)
            assert r.status_code == 200, r.text
        yield c
    _reset_state()


async def _bearer(c: AsyncClient, key: str) -> dict:
    r = await c.post("/v1/auth/token", json={"api_key": key})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _join(c: AsyncClient, room: str, name: str, **mint) -> tuple[str, dict]:
    """Mint an invite to *room* and redeem it as *name*: (invite id, auth header)."""
    r = await c.post("/v1/join/mint", json={"room": room, **mint}, headers=ADMIN)
    assert r.status_code == 200, r.text
    inv_id = r.json()["invite_id"]
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    key = (await c.post("/v1/join/redeem", json={"code": payload["c"], "name": name})
           ).json()["participant_key"]
    return inv_id, await _bearer(c, key)


async def _agent(c: AsyncClient, owner: dict, name: str, room: str = "build") -> dict:
    """*owner* claims agent *name* and adds it to *room*; the agent's auth header."""
    r = await c.post("/v1/auth/claim-name", json={"name": name}, headers=owner)
    assert r.status_code == 200, r.text
    agent = await _bearer(c, r.json()["participant_key"])
    r = await c.post(f"/rooms/{room}/join", json={"participant": name}, headers=owner)
    assert r.status_code == 200, r.text
    return agent


async def _say(c: AsyncClient, who: dict, name: str, room: str = "build") -> int:
    r = await c.post(f"/rooms/{room}/messages", json={"from_name": name, "content": "hi"},
                     headers=who)
    return r.status_code


async def test_an_invitee_adds_their_own_agent_and_it_posts(client: AsyncClient) -> None:
    _, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    assert await _say(client, agent, "bob-codex") == 200
    roles = (await client.get("/rooms/build", headers=ADMIN)).json()["member_roles"]
    assert roles["bob-codex"] == "member"


async def test_the_name_must_be_the_owners(client: AsyncClient) -> None:
    _, bob = await _join(client, "build", "bob")
    for name in ("carol-codex", "bobcodex", "bob-", "alice"):
        r = await client.post("/v1/auth/claim-name", json={"name": name}, headers=bob)
        assert r.status_code in (400, 403, 409, 422), (name, r.text)


async def test_a_name_the_relay_already_answers_to_is_refused(client) -> None:
    _, bob = await _join(client, "build", "bob")
    # alice (the secret holder) put bob-codex in a room before bob claimed it.
    await client.post("/rooms/ops/join", json={"participant": "bob-codex"}, headers=ADMIN)
    r = await client.post("/v1/auth/claim-name", json={"name": "bob-codex"}, headers=bob)
    assert r.status_code == 409, r.text


async def test_an_agent_can_mint_neither_agents_nor_invites(client: AsyncClient) -> None:
    _, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    r = await client.post("/v1/auth/claim-name", json={"name": "bob-codex-2"}, headers=agent)
    assert r.status_code == 403, r.text
    r = await client.post("/v1/join/mint", json={"room": "build"}, headers=agent)
    assert r.status_code == 403, r.text


async def test_at_most_eight_agents_per_owner(client: AsyncClient) -> None:
    _, bob = await _join(client, "build", "bob")
    store = app.state.participant_keys
    owner = store.info("bob")
    for i in range(member_agents.MAX_AGENTS):
        member_agents.mint(store, "bob", owner["claim_id"], f"bob-a{i}")
    r = await client.post("/v1/auth/claim-name", json={"name": "bob-one-more"}, headers=bob)
    assert r.status_code == 409 and "8 agents" in r.text


async def test_an_owner_adds_agents_only_to_their_rooms(client: AsyncClient) -> None:
    _, bob = await _join(client, "build", "bob")
    r = await client.post("/v1/auth/claim-name", json={"name": "bob-codex"}, headers=bob)
    assert r.status_code == 200
    r = await client.post("/rooms/ops/join", json={"participant": "bob-codex"}, headers=bob)
    assert r.status_code == 403, r.text  # bob isn't in #ops
    _, carol = await _join(client, "build", "carol")
    r = await client.post("/rooms/build/join", json={"participant": "bob-codex"}, headers=carol)
    assert r.status_code == 403, r.text  # not carol's agent


async def test_an_observers_agent_only_reads(client: AsyncClient) -> None:
    _, olive = await _join(client, "build", "olive", role="observer")
    r = await client.post("/v1/auth/claim-name", json={"name": "olive-codex"}, headers=olive)
    assert r.status_code == 200
    r = await client.post("/rooms/build/join", json={"participant": "olive-codex"},
                          headers=olive)
    assert r.status_code == 403, r.text  # an observer can't bring a writer in


async def test_kicking_the_owner_takes_their_agents_out(client: AsyncClient) -> None:
    _, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    r = await client.post("/rooms/build/kick", json={"participant": "bob", "requested_by": "alice"},
                          headers=ADMIN)
    assert r.status_code == 200, r.text
    members = (await client.get("/rooms/build", headers=ADMIN)).json()["members"]
    assert "bob" not in members and "bob-codex" not in members
    assert await _say(client, agent, "bob-codex") == 403


async def test_revoking_the_owners_invite_takes_their_agents_out(client) -> None:
    inv, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    r = await client.post(f"/v1/join/invites/{inv}/revoke", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert (await client.get("/rooms/build/history", headers=agent)).status_code == 403


async def test_an_agent_left_behind_still_cant_act(client: AsyncClient) -> None:
    """Defense in depth: if the owner's row goes some other way, the agent's
    own membership row isn't enough."""
    _, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    svc = app.state.room_service
    rid, _ = await svc.get("_legacy", "build")
    await svc.leave("_legacy", rid, "bob")  # bypasses the cascade
    assert (await client.get("/rooms/build/history", headers=agent)).status_code == 403
    assert await _say(client, agent, "bob-codex") == 403


async def test_releasing_the_owner_kills_their_agents(client: AsyncClient) -> None:
    _, bob = await _join(client, "build", "bob")
    agent = await _agent(client, bob, "bob-codex")
    store = app.state.participant_keys
    assert store.release("bob")
    assert store.info("bob-codex") is None  # the name is free again
    assert (await client.get("/rooms/build/history", headers=agent)).status_code == 401


async def test_a_reclaimed_owner_name_doesnt_revive_old_agents(tmp_path) -> None:
    store = ParticipantKeyStore(tmp_path / "s")
    store.claim("bob", kind="human")
    first = store.info("bob")["claim_id"]
    member_agents.mint(store, "bob", first, "bob-codex")
    agent_gen = store.info("bob-codex")["claim_id"]
    # bob's row changes generation without going through release (a migration,
    # a hand edit): the agent must not outlive the generation it was minted under.
    with store._connect() as db:
        db.execute("UPDATE claims SET claim_id = 'other' WHERE name = 'bob'")
    assert not store.is_current("bob-codex", agent_gen)
