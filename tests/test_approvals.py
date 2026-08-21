"""L3 (WAKE_REBUILD_SPEC): human-in-the-loop tool approvals.

A headless agent hitting a permission prompt used to stall invisibly until
the wall-clock kill. Now the prompt becomes a room event any member can
answer from any machine.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app

HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _room(client: AsyncClient) -> str:
    resp = await client.post(
        "/rooms", json={"name": "apr", "created_by": "arav"}, headers=HEADERS
    )
    rid = resp.json()["id"]
    await client.post(f"/rooms/{rid}/join", json={"participant": "arav-claude"},
                      headers=HEADERS)
    return rid


async def _create(client: AsyncClient, rid: str, **kw) -> dict:
    body = {"room_id": rid, "agent": "arav-claude", "tool_name": "Bash",
            "tool_input": "rm -rf build/"}
    body.update(kw)
    resp = await client.post("/v1/approvals", json=body, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_request_then_approve_roundtrip(client):
    rid = await _room(client)
    rec = await _create(client, rid)
    assert rec["status"] == "pending"
    assert rec["id"].startswith("apr_")

    poll = await client.get(f"/v1/approvals/{rec['id']}", headers=HEADERS)
    assert poll.json()["status"] == "pending"

    dec = await client.post(
        f"/v1/approvals/{rec['id']}/decision",
        json={"approve": True, "reason": "looks fine"}, headers=HEADERS,
    )
    assert dec.status_code == 200
    assert dec.json()["status"] == "approved"
    assert dec.json()["reason"] == "looks fine"

    poll = await client.get(f"/v1/approvals/{rec['id']}", headers=HEADERS)
    assert poll.json()["status"] == "approved"


async def test_deny_is_recorded(client):
    rid = await _room(client)
    rec = await _create(client, rid)
    dec = await client.post(f"/v1/approvals/{rec['id']}/decision",
                            json={"approve": False}, headers=HEADERS)
    assert dec.json()["status"] == "denied"


async def test_decision_is_idempotent(client):
    """A settled decision never flips — the agent may already have acted."""
    rid = await _room(client)
    rec = await _create(client, rid)
    await client.post(f"/v1/approvals/{rec['id']}/decision",
                      json={"approve": True}, headers=HEADERS)
    second = await client.post(f"/v1/approvals/{rec['id']}/decision",
                               json={"approve": False}, headers=HEADERS)
    assert second.json()["status"] == "approved"


async def test_input_is_previewed_not_stored_verbatim(client):
    """Approval records are readable by every room member, so a long tool
    input is truncated, not echoed in full."""
    rid = await _room(client)
    rec = await _create(client, rid, tool_input="x" * 5000)
    assert len(rec["input_preview"]) <= 210
    assert rec["input_preview"].endswith("…")


async def test_pending_list_filters_by_room(client):
    rid = await _room(client)
    await _create(client, rid, tool_name="Bash")
    await _create(client, rid, tool_name="Edit")
    resp = await client.get("/v1/approvals", headers=HEADERS)
    assert len(resp.json()["pending"]) == 2
    resp = await client.get("/v1/approvals?room=nonexistent", headers=HEADERS)
    assert resp.json()["pending"] == []


async def test_unknown_id_404s(client):
    resp = await client.get("/v1/approvals/apr_missing", headers=HEADERS)
    assert resp.status_code == 404
    resp = await client.post("/v1/approvals/apr_missing/decision",
                             json={"approve": True}, headers=HEADERS)
    assert resp.status_code == 404


async def test_approval_posts_a_room_message(client):
    """The request must be visible in the room, not just over SSE."""
    rid = await _room(client)
    rec = await _create(client, rid)
    hist = await client.get(f"/rooms/{rid}/history?limit=10", headers=HEADERS)
    bodies = [m["content"] for m in hist.json()]
    assert any("approval needed" in b and rec["id"] in b for b in bodies)


async def test_agent_cannot_self_approve(client, monkeypatch):
    """The gate is only real if the agent can't wave itself through."""
    from quorus.auth.middleware import AuthContext

    rid = await _room(client)
    rec = await _create(client, rid)

    # Simulate the agent's own (non-legacy) identity deciding.
    import quorus.routes.approvals as approvals_mod

    async def fake_auth():
        return AuthContext(sub="arav-claude", tenant_id=None, is_legacy=False,
                           role="member")

    app.dependency_overrides[approvals_mod.verify_auth] = fake_auth
    try:
        resp = await client.post(f"/v1/approvals/{rec['id']}/decision",
                                 json={"approve": True}, headers=HEADERS)
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()

    poll = await client.get(f"/v1/approvals/{rec['id']}", headers=HEADERS)
    assert poll.json()["status"] == "pending"


async def test_mcp_bridge_end_to_end(client, monkeypatch):
    """L3 e2e: the MCP permission bridge blocks, a human approves, and the
    bridge returns allow — the full loop a woken agent actually walks."""
    import asyncio as _asyncio
    import sys

    sys.path.insert(0, "packages/mcp")
    from quorus_mcp import phase1_tools as p1

    rid = await _room(client)

    class _FakeSrv:
        RELAY_URL = ""
        INSTANCE_NAME = "arav-claude"

        @staticmethod
        def _get_http_client():
            return client

        @staticmethod
        async def _auth_headers_async():
            return HEADERS

        @staticmethod
        async def _refresh_jwt_on_401():
            return False

    monkeypatch.setattr(p1, "_srv", lambda: _FakeSrv)
    monkeypatch.setattr(p1, "APPROVAL_POLL_INTERVAL_S", 0.01)

    async def human_approves():
        for _ in range(200):
            await _asyncio.sleep(0.01)
            resp = await client.get("/v1/approvals", headers=HEADERS)
            pending = resp.json()["pending"]
            if pending:
                await client.post(
                    f"/v1/approvals/{pending[0]['id']}/decision",
                    json={"approve": True, "reason": "ok"}, headers=HEADERS,
                )
                return

    human = _asyncio.create_task(human_approves())
    decision = await p1.approve("Bash", "pytest -q", room_id=rid)
    await human
    assert decision == {"behavior": "allow", "updatedInput": "pytest -q"}


async def test_mcp_bridge_fails_closed_on_relay_error(monkeypatch):
    """A broken relay must DENY, never silently widen permissions."""
    import sys
    sys.path.insert(0, "packages/mcp")
    from quorus_mcp import phase1_tools as p1

    async def boom(*a, **k):
        raise RuntimeError("relay down")

    monkeypatch.setattr(p1, "request_approval", boom)
    decision = await p1.approve("Bash", "rm -rf /", room_id="dev")
    assert decision["behavior"] == "deny"
    assert "unreachable" in decision["message"]


async def test_mcp_bridge_denies_without_room(monkeypatch):
    import sys
    sys.path.insert(0, "packages/mcp")
    from quorus_mcp import phase1_tools as p1

    monkeypatch.delenv("QUORUS_APPROVAL_ROOM", raising=False)
    decision = await p1.approve("Bash", "ls")
    assert decision["behavior"] == "deny"
