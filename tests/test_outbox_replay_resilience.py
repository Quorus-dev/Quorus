"""Outbox/durable-queue replay resilience smoke tests -- LOAD-BEARING WALL of
Kliment Demo Beat 2 (May 7 2026, Plan v8). Test 1 runs against the in-memory
backend (no Postgres needed). Test 2 verifies the audit hash chain across the
disconnect; it skips without DATABASE_URL because in-memory mode constructs no
AuditService (see relay.py::_init_services). The actual AuditEvent enum uses
MESSAGE_CREATED / MESSAGE_QUEUED / FANOUT_STARTED / FANOUT_COMPLETED / DELIVERED
(not the prompt's CREATED/FANOUT_QUEUED/FANOUT_PUBLISHED vocabulary).
"""
from __future__ import annotations

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app

HEADERS = {"Authorization": "Bearer test-secret"}
TENANT = "_legacy"
SENDER = "alice"
RECIPIENT = "bob"
ROOM = "kliment-replay"


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _make_room(client: AsyncClient) -> str:
    r = await client.post("/rooms", json={"name": ROOM, "created_by": SENDER}, headers=HEADERS)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    j = await client.post(f"/rooms/{rid}/join", json={"participant": RECIPIENT}, headers=HEADERS)
    assert j.status_code == 200, j.text
    return rid


async def _send(client: AsyncClient, room_id: str, content: str) -> str:
    r = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": SENDER, "content": content},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _drain(q: asyncio.Queue, expected: int, timeout: float = 4.0) -> list[str]:
    out: list[str] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while len(out) < expected:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AssertionError(f"timeout drained {len(out)}/{expected}: {out!r}")
        try:
            msg = await asyncio.wait_for(q.get(), timeout=remaining)
        except asyncio.TimeoutError as e:
            raise AssertionError(f"timeout draining SSE: {len(out)}/{expected}: {out!r}") from e
        out.append(msg["content"])
    return out


# Test 1 -- LOAD-BEARING WALL of the Kliment demo claim.
async def test_subscriber_disconnect_messages_replay_on_reconnect(client: AsyncClient):
    """Beat 2 contract: N + M messages arrive in order across a disconnect.
    bob registers SSE -> sees N -> his queue unregisters (= SIGTERM) -> M
    more fan out to his per-recipient deque (the durable outbox analogue)
    -> bob reconnects + drains backlog via long-poll -> sees N+M+1 in order.
    """
    sse_svc = app.state.sse_service
    room_id = await _make_room(client)
    q1 = sse_svc.register_queue(TENANT, RECIPIENT)
    n = 3
    for i in range(n):
        await _send(client, room_id, f"msg-{i:02d}")
    seen_phase1 = await _drain(q1, n, timeout=4.0)
    assert seen_phase1 == [f"msg-{i:02d}" for i in range(n)], seen_phase1
    # Drain anything bob accumulated in the per-recipient deque before the
    # disconnect (long-poll ack=server) so phase-3 backlog is the only thing
    # in the deque on reconnect.
    pre = await client.get(f"/messages/{RECIPIENT}?ack=server", headers=HEADERS)
    assert pre.status_code == 200
    # bob's daemon dies: unregister his SSE queue.
    sse_svc.unregister_queue(TENANT, RECIPIENT, q1)
    # Fan-out keeps writing to bob's per-recipient queue while he's offline.
    m = 4
    for i in range(n, n + m):
        await _send(client, room_id, f"msg-{i:02d}")
    backend = app.state.backends.messages
    backlog_len = len(backend._queues.get((TENANT, RECIPIENT), []))
    assert backlog_len == m, f"expected {m} queued while disconnected, got {backlog_len}"
    # Reconnect SSE + drain backlog via long-poll = outbox replay.
    q2 = sse_svc.register_queue(TENANT, RECIPIENT)
    resp = await client.get(f"/messages/{RECIPIENT}?ack=server", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    replayed = [m["content"] for m in resp.json()]
    assert replayed == [f"msg-{i:02d}" for i in range(n, n + m)], replayed
    # Live message after reconnect proves SSE is back.
    await _send(client, room_id, "msg-post-reconnect")
    live = await _drain(q2, 1, timeout=4.0)
    assert live == ["msg-post-reconnect"]
    sse_svc.unregister_queue(TENANT, RECIPIENT, q2)
    # Full bob-saw list = N + M + 1 in send order.
    bob_saw = seen_phase1 + replayed + live
    expected = [f"msg-{i:02d}" for i in range(n + m)] + ["msg-post-reconnect"]
    assert bob_saw == expected, f"end-to-end ordering broken: {bob_saw!r}"


# Test 2 -- audit hash chain intact (Postgres-only).
_NO_DB = not os.environ.get("DATABASE_URL")
_REASON = "Audit ledger requires DATABASE_URL (in-memory mode has no AuditService)."


@pytest.mark.skipif(_NO_DB, reason=_REASON)
async def test_audit_chain_intact_after_subscriber_disconnect(client: AsyncClient):
    """When Postgres is wired, the audit chain records lifecycle events AND
    its hash chain still verifies even with a mid-stream disconnect."""
    import uuid as _uuid

    from quorus.models.audit import AuditEvent
    audit_svc = app.state.audit_service
    assert audit_svc is not None, "audit_service must exist when DATABASE_URL set"
    sse_svc = app.state.sse_service
    room_id = await _make_room(client)
    q1 = sse_svc.register_queue(TENANT, RECIPIENT)
    msg_id_1 = await _send(client, room_id, "audit-phase1")
    await _drain(q1, 1, timeout=4.0)
    sse_svc.unregister_queue(TENANT, RECIPIENT, q1)
    msg_id_2 = await _send(client, room_id, "audit-phase2")
    await asyncio.sleep(0.5)  # let async audit writes flush
    timeline_2 = await audit_svc.get_message_timeline(TENANT, _uuid.UUID(msg_id_2))
    event_types = {e["event_type"] for e in timeline_2}
    assert AuditEvent.MESSAGE_CREATED.value in event_types, \
        f"missing CREATED for offline-window msg: {event_types!r}"
    chain = await audit_svc.verify_chain(TENANT)
    assert chain.get("verified") is True, f"audit chain broken: {chain!r}"
    timeline_1 = await audit_svc.get_message_timeline(TENANT, _uuid.UUID(msg_id_1))
    assert len(timeline_1) >= 1, f"phase1 timeline empty: {timeline_1!r}"
