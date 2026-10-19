"""Observers: invitees who may read one room but change nothing (0.6.0).

Codex's threat model: an observer is an untrusted reader who may script the HTTP
API directly or keep an old JWT. The relay reads the role from the room's
membership row on every write (room_auth.require_room_writer), so the UI is not
the boundary, a downgrade applies to the next request, and a role in another
room never counts. Writes not tied to one room (DMs, new rooms, capabilities,
webhooks) refuse a caller who only observes (require_not_observer_only).
"""

from __future__ import annotations

import inspect

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.relay import _reset_state, app
from quorus.routes.room_auth import OBSERVER_DENIED

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


async def _join(c: AsyncClient, room: str, name: str, role: str) -> dict:
    r = await c.post("/v1/join/mint", json={"room": room, "role": role}, headers=ADMIN)
    assert r.status_code == 200, r.text
    payload = (await c.get(f"/v1/join/resolve/{r.json()['code']}")).json()["payload"]
    redeem = await c.post("/v1/join/redeem", json={"code": payload["c"], "name": name})
    assert redeem.status_code == 200 and redeem.json()["role"] == role, redeem.text
    jwt = (await c.post("/v1/auth/token",
                        json={"api_key": redeem.json()["participant_key"]})).json()["token"]
    return {"Authorization": f"Bearer {jwt}"}


# Every write an observer could try, with a body the relay accepts from a member.
WRITES = [
    ("POST", "/rooms/build/messages", lambda n: {"from_name": n, "content": "hi"}),
    ("POST", "/rooms/build/lock", lambda n: {"file_path": "a.py", "claimed_by": n}),
    ("PATCH", "/rooms/build/state/goal", lambda n: {"goal": "x"}),
    ("POST", "/rooms/build/state/decisions", lambda n: {"decision": "x", "decided_by": n}),
    ("POST", "/v1/rooms/build/tools",
     lambda n: {"name": "t1", "url": "https://example.com/t", "description": "d"}),
    ("POST", "/v1/work_queue/build", lambda n: {"op": "add", "summary": "t", "requested_by": n}),
    ("POST", "/v1/approvals",
     lambda n: {"room_id": "build", "agent": n, "tool_name": "Bash", "input": {}}),
    ("POST", "/v1/social/claim", lambda n: {"actor": n, "room_id": "build", "payload": {
        "task_id": "t1", "eta_seconds": 60, "scope": "a.py"}}),
    ("PUT", "/v1/memory/{n}/build/k", lambda n: {"value": "v"}),
    ("POST", "/v1/bid",
     lambda n: {"room_id": "build", "participant": n, "message_id": "m1", "bid": 0.5}),
    ("POST", "/v1/claim", lambda n: {"room_id": "build", "message_id": "m1"}),
    ("POST", "/v1/triage", lambda n: {"room_id": "build", "message_id": "m1", "content": "hi"}),
    ("POST", "/v1/rooms/build/bundles", None),
    ("POST", "/v1/dm", lambda n: {"from": n, "to": "arav", "content": "hi"}),
    ("POST", "/messages", lambda n: {"from_name": n, "to": "arav", "content": "hi"}),
    ("POST", "/rooms", lambda n: {"name": "new-" + n, "created_by": n}),
    ("POST", "/v1/capabilities/{n}", lambda n: {"description": "d"}),
    ("POST", "/webhooks", lambda n: {"instance_name": n, "callback_url": "https://example.com/h"}),
    ("POST", "/rooms/build/webhooks",
     lambda n: {"callback_url": "https://example.com/h", "registered_by": n}),
]


async def _send(c: AsyncClient, method: str, path: str, body, name: str, headers: dict):
    path = path.replace("{n}", name)
    if body is None:  # a bundle upload is a raw body
        return await c.request(method, path, content=b"x", headers=headers)
    return await c.request(method, path, json=body(name), headers=headers)


async def _snapshot(c: AsyncClient) -> tuple:
    history = (await c.get("/rooms/build/history", headers=ADMIN)).json()
    state = (await c.get("/rooms/build/state", headers=ADMIN)).json()
    rooms = sorted(r["name"] for r in (await c.get("/rooms", headers=ADMIN)).json())
    return len(history), state.get("goal"), len(state.get("decisions") or []), rooms


async def test_an_observer_reads_the_room(client: AsyncClient) -> None:
    olive = await _join(client, "build", "olive", "observer")
    assert (await client.get("/rooms/build/history", headers=olive)).status_code == 200
    assert (await client.get("/rooms/build/state", headers=olive)).status_code == 200
    assert (await client.get("/rooms/ops/history", headers=olive)).status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=[f"{m} {p}" for m, p, _ in WRITES])
async def test_an_observer_changes_nothing(client: AsyncClient, method, path, body) -> None:
    olive = await _join(client, "build", "olive", "observer")
    before = await _snapshot(client)
    r = await _send(client, method, path, body, "olive", olive)
    assert r.status_code == 403 and r.json()["detail"] == OBSERVER_DENIED, r.text
    assert await _snapshot(client) == before


@pytest.mark.parametrize(("method", "path", "body"),
                         [w for w in WRITES if w[2] is not None and "tools" not in w[1]],
                         ids=[f"{m} {p}" for m, p, b in WRITES if b and "tools" not in p])
async def test_a_member_is_not_mistaken_for_an_observer(client, method, path, body) -> None:
    mark = await _join(client, "build", "mark", "member")
    r = await _send(client, method, path, body, "mark", mark)
    assert r.status_code != 403 or r.json().get("detail") != OBSERVER_DENIED, r.text


async def test_a_downgrade_applies_to_the_next_request(client: AsyncClient) -> None:
    mark = await _join(client, "build", "mark", "member")
    msg = {"from_name": "mark", "content": "hi"}
    assert (await client.post("/rooms/build/messages", json=msg, headers=mark)).status_code == 200
    rooms = app.state.room_service
    rid, _ = await rooms.get("_legacy", "build")
    await rooms.leave("_legacy", rid, "mark")
    await rooms.join("_legacy", rid, "mark", "observer", 50)
    r = await client.post("/rooms/build/messages", json=msg, headers=mark)  # same old JWT
    assert r.status_code == 403 and r.json()["detail"] == OBSERVER_DENIED


async def test_an_unknown_role_cannot_write(client: AsyncClient) -> None:
    mark = await _join(client, "build", "mark", "member")
    rooms = app.state.room_service
    rid, _ = await rooms.get("_legacy", "build")
    await rooms.leave("_legacy", rid, "mark")
    await rooms.join("_legacy", rid, "mark", "superuser", 50)
    r = await client.post("/rooms/build/messages", json={"from_name": "mark", "content": "x"},
                          headers=mark)
    assert r.status_code == 403


async def test_a_role_in_another_room_never_counts_here(client: AsyncClient) -> None:
    olive = await _join(client, "build", "olive", "observer")
    rooms = app.state.room_service
    ops, _ = await rooms.get("_legacy", "ops")
    await rooms.join("_legacy", ops, "olive", "member", 50)
    msg = {"from_name": "olive", "content": "hi"}
    assert (await client.post("/rooms/ops/messages", json=msg, headers=olive)).status_code == 200
    r = await client.post("/rooms/build/messages", json=msg, headers=olive)
    assert r.status_code == 403 and r.json()["detail"] == OBSERVER_DENIED


async def test_an_observer_member_cannot_decide_an_approval(client: AsyncClient) -> None:
    olive = await _join(client, "build", "olive", "observer")
    mark = await _join(client, "build", "mark", "member")
    asked = await client.post("/v1/approvals", json={
        "room_id": "build", "agent": "mark", "tool_name": "Bash", "input": {}}, headers=mark)
    assert asked.status_code == 200, asked.text
    approval_id = asked.json().get("id") or asked.json().get("approval_id")
    r = await client.post(f"/v1/approvals/{approval_id}/decision",
                          json={"approve": True}, headers=olive)
    assert r.status_code == 403 and r.json()["detail"] == OBSERVER_DENIED, r.text


# ---------------------------------------------------------------------------
# The route-table walk: a new write route can't skip the guard silently.
# ---------------------------------------------------------------------------

GUARDS = ("require_room_writer(", "require_not_observer_only(", "_writer(")
# Writes that need no observer guard, and why.
NOT_ROOM_WRITES = {
    ("POST", "/admin/login"): "admin dashboard sign-in, not a participant route",
    ("POST", "/admin/logout"): "admin dashboard sign-out",
    ("POST", "/heartbeat"): "your own presence",
    ("POST", "/invite/{room_name}/join"): "410 on shared-secret relays; API-key invite path",
    ("POST", "/messages/{recipient}/ack"): "acknowledging your own inbox",
    ("DELETE", "/rooms/{room_id}"): "room creator or admin only; an observer is neither",
    ("PATCH", "/rooms/{room_id}"): "room creator or admin only",
    ("POST", "/rooms/{room_id}/join"): "room creator or admin only; self-join refused",
    ("POST", "/rooms/{room_id}/leave"): "leaving is always allowed",
    ("POST", "/rooms/{room_id}/kick"): "room creator or admin only",
    ("DELETE", "/rooms/{room_id}/webhooks"): "removes only the caller's own webhook",
    ("POST", "/rooms/{room_id}/webhooks"): "checks the registrant's room role inline",
    ("POST", "/stream/token"): "a token to read your own stream",
    ("POST", "/v1/approvals/{approval_id}/decision"): "checks the decider's room role inline",
    ("POST", "/v1/audit/mcp-tool-call"): "a receipt of the caller's own tool call",
    ("POST", "/v1/auth/claim-name"): "identity bootstrap, before any room",
    ("POST", "/v1/auth/register-agent"): "identity bootstrap",
    ("POST", "/v1/auth/signup"): "identity bootstrap",
    ("POST", "/v1/auth/token"): "sign-in",
    ("DELETE", "/v1/dm/{participant}"): "purging your own DM thread",
    ("POST", "/v1/join/invites/{invite_id}/revoke"): "relay owner only",
    ("POST", "/v1/join/mint"): "admin only",
    ("POST", "/v1/join/redeem"): "the invite code is the credential",
    ("DELETE", "/v1/memory/{participant}/{room_id}/{key}"): "guarded: require_room_writer",
    ("DELETE", "/v1/rooms/{room_id}/tools/{name}"): "guarded: require_room_writer",
    ("DELETE", "/rooms/{room_id}/lock/{file_path}"): "guarded: require_room_writer",
    ("POST", "/v1/tenants"): "tenant admin only",
    ("POST", "/v1/tenants/{slug}/participants"): "tenant admin only",
    ("POST", "/v1/tenants/{slug}/participants/{name}/keys"): "tenant admin only",
    ("DELETE", "/v1/tenants/{slug}/participants/{name}/keys/{key_id}"): "tenant admin only",
    ("DELETE", "/webhooks/{instance_name}"): "removes only the caller's own webhook",
}


def _routes(routes):
    from fastapi.routing import APIRoute

    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from _routes(r.original_router.routes)
        elif hasattr(r, "routes"):
            yield from _routes(r.routes)


def test_every_write_route_is_guarded_or_explained() -> None:
    unexplained = []
    for route in _routes(app.router.routes):
        for method in sorted(route.methods - {"GET", "HEAD", "OPTIONS"}):
            key = (method, route.path.replace(":path}", "}"))
            source = inspect.getsource(route.endpoint)
            if key in NOT_ROOM_WRITES or any(g in source for g in GUARDS):
                continue
            unexplained.append(key)
    assert unexplained == [], (
        "new write routes must call require_room_writer / require_not_observer_only, "
        f"or say why not in NOT_ROOM_WRITES: {unexplained}")


async def test_quorus_share_observer_mints_an_observer_invite(client, monkeypatch, capsys) -> None:
    import argparse
    import asyncio as _asyncio

    import httpx

    from quorus import cli

    def post(url, **kw):  # route the CLI's sync httpx call into the in-process relay
        return _asyncio.run_coroutine_threadsafe(
            client.post(url.replace("http://t", ""), json=kw.get("json"), headers=ADMIN),
            loop).result()

    loop = _asyncio.get_running_loop()
    monkeypatch.setattr(cli, "RELAY_URL", "http://t")
    monkeypatch.setattr(cli, "RELAY_SECRET", "test-secret")
    monkeypatch.setattr(httpx, "post", post)
    args = argparse.Namespace(room="build", ttl=1, uses=1, copy=False, observer=True)
    await _asyncio.to_thread(cli._cmd_share, args)
    assert "as an observer" in " ".join(capsys.readouterr().out.split())
    listed = (await client.get("/v1/join/invites", headers=ADMIN)).json()
    assert listed[0]["role"] == "observer"


# ---------------------------------------------------------------------------
# Codex's review of 910e2f69: three boundary gaps.
# ---------------------------------------------------------------------------


async def test_an_observer_who_leaves_every_room_is_still_an_observer(client) -> None:
    olive = await _join(client, "build", "olive", "observer")
    r = await client.post("/rooms/build/leave", json={"participant": "olive"}, headers=olive)
    assert r.status_code == 200, r.text
    made = await client.post("/rooms", json={"name": "olives", "created_by": "olive"},
                             headers=olive)
    assert made.status_code == 403 and made.json()["detail"] == OBSERVER_DENIED
    dm = await client.post("/v1/dm", json={"from": "olive", "to": "arav", "content": "hi"},
                           headers=olive)
    assert dm.status_code == 403


async def test_an_observer_cannot_read_approval_previews(client: AsyncClient) -> None:
    olive = await _join(client, "build", "olive", "observer")
    mark = await _join(client, "build", "mark", "member")
    asked = await client.post("/v1/approvals", json={
        "room_id": "build", "agent": "mark", "tool_name": "Bash",
        "tool_input": {"command": "cat secrets.txt"}}, headers=mark)
    approval_id = asked.json().get("id") or asked.json().get("approval_id")
    assert (await client.get(f"/v1/approvals/{approval_id}", headers=olive)).status_code == 404
    listed = (await client.get("/v1/approvals", headers=olive)).json()["pending"]
    assert listed == []
    assert (await client.get(f"/v1/approvals/{approval_id}", headers=mark)).status_code == 200


async def test_an_observer_cannot_download_bundles(client: AsyncClient, monkeypatch) -> None:
    olive = await _join(client, "build", "olive", "observer")
    r = await client.get("/v1/rooms/build/bundles/b1", headers=olive)
    # The role check runs before anything else, so even a relay without bundle
    # storage answers an observer with the observer refusal.
    assert r.status_code == 403 and r.json()["detail"] == OBSERVER_DENIED


async def test_approval_previews_never_reach_room_history_or_observer_streams(client) -> None:
    """Codex, review of 416d2d55: the preview still went to every member's SSE
    stream and into the room's chat line, which observers can read."""
    olive = await _join(client, "build", "olive", "observer")
    mark = await _join(client, "build", "mark", "member")
    pushed = []
    sse = app.state.sse_service
    real_push = sse.push

    def spy(tid, member, payload):
        pushed.append((member, payload))
        return real_push(tid, member, payload)

    sse.push = spy
    try:
        r = await client.post("/v1/approvals", json={
            "room_id": "build", "agent": "mark", "tool_name": "Bash",
            "tool_input": {"command": "cat top-secret.txt"}}, headers=mark)
    finally:
        sse.push = real_push
    assert r.status_code == 200, r.text
    history = (await client.get("/rooms/build/history", headers=olive)).text
    assert "top-secret" not in history and "approval needed" in history
    approval_events = [(m, p) for m, p in pushed if p.get("event") == "approval_requested"]
    assert "olive" not in [m for m, _ in approval_events]
    assert any(m == "mark" and "top-secret" in str(p) for m, p in approval_events)
