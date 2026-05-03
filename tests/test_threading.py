"""Tests for Stream B threading.

Coverage:
* explicit thread_root_id round-trips on POST /rooms/{room}/messages
* reply_to chain auto-resolves the root_id when not explicitly passed
* deep thread (5 levels) preserves the root for every reply
* GET /rooms/{room}/threads/{root_id} returns all messages in the thread
* messages with no root_id stay out of the thread (no contamination)
* mixed threaded + non-threaded posts in the same feed both render
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app

HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
async def _clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def room_id(client: AsyncClient) -> str:
    resp = await client.post(
        "/rooms",
        json={"name": "thread-test-room", "created_by": "alice"},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    rid = resp.json()["id"]
    for who in ("bob", "carol"):
        join = await client.post(
            f"/rooms/{rid}/join",
            json={"participant": who, "role": "member"},
            headers=HEADERS,
        )
        assert join.status_code == 200
    return rid


# ---------------------------------------------------------------------------
# 1. Explicit root_id round-trips
# ---------------------------------------------------------------------------


async def test_explicit_thread_root_id_roundtrip(
    client: AsyncClient, room_id: str,
):
    parent = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "kicking off thread"},
        headers=HEADERS,
    )
    assert parent.status_code == 200, parent.text
    parent_id = parent.json()["id"]

    reply = await client.post(
        f"/rooms/{room_id}/messages",
        json={
            "from_name": "bob", "content": "in-thread reply",
            "thread_root_id": parent_id,
        },
        headers=HEADERS,
    )
    assert reply.status_code == 200
    body = reply.json()
    assert body.get("thread_root_id") == parent_id


# ---------------------------------------------------------------------------
# 2. Auto-resolve via reply_to
# ---------------------------------------------------------------------------


async def test_root_id_inferred_from_reply_to_chain(
    client: AsyncClient, room_id: str,
):
    parent = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "root"},
        headers=HEADERS,
    )
    parent_id = parent.json()["id"]

    # Use reply_to without thread_root_id — root_id should resolve to
    # the parent's own id (since the parent IS the root).
    child = await client.post(
        f"/rooms/{room_id}/messages",
        json={
            "from_name": "bob", "content": "reply via reply_to",
            "reply_to": parent_id,
        },
        headers=HEADERS,
    )
    body = child.json()
    assert body.get("thread_root_id") == parent_id


# ---------------------------------------------------------------------------
# 3. Deep thread (5 levels)
# ---------------------------------------------------------------------------


async def test_deep_thread_preserves_root_id(
    client: AsyncClient, room_id: str,
):
    # 5 levels: root, then 4 nested replies. Each carries root_id.
    root = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "L0"},
        headers=HEADERS,
    )
    root_id = root.json()["id"]

    parent_id = root_id
    for level in range(1, 5):
        reply = await client.post(
            f"/rooms/{room_id}/messages",
            json={
                "from_name": "bob",
                "content": f"L{level}",
                "reply_to": parent_id,
                "thread_root_id": root_id,
            },
            headers=HEADERS,
        )
        body = reply.json()
        assert body.get("thread_root_id") == root_id, f"level {level}"
        parent_id = body["id"]


# ---------------------------------------------------------------------------
# 4. Thread fetch via GET /rooms/{room}/threads/{root_id}
# ---------------------------------------------------------------------------


async def test_thread_fetch_returns_all_replies(
    client: AsyncClient, room_id: str,
):
    root = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "thread root"},
        headers=HEADERS,
    )
    root_id = root.json()["id"]

    for who in ("bob", "carol", "alice"):
        await client.post(
            f"/rooms/{room_id}/messages",
            json={
                "from_name": who, "content": f"{who}'s reply",
                "thread_root_id": root_id,
            },
            headers=HEADERS,
        )

    resp = await client.get(
        f"/rooms/{room_id}/threads/{root_id}", headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["root_id"] == root_id
    contents = [m.get("content") for m in body["messages"]]
    # 1 root + 3 replies = 4
    assert len(body["messages"]) == 4
    assert contents[0] == "thread root"
    assert "bob's reply" in contents
    assert "carol's reply" in contents
    assert "alice's reply" in contents


# ---------------------------------------------------------------------------
# 5. Non-threaded messages stay out of the thread
# ---------------------------------------------------------------------------


async def test_non_threaded_messages_excluded(
    client: AsyncClient, room_id: str,
):
    root = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "thread root"},
        headers=HEADERS,
    )
    root_id = root.json()["id"]

    # In-thread reply
    await client.post(
        f"/rooms/{room_id}/messages",
        json={
            "from_name": "bob", "content": "in-thread",
            "thread_root_id": root_id,
        },
        headers=HEADERS,
    )
    # Out-of-thread message — should NOT appear in the thread fetch.
    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "carol", "content": "stranger to the thread"},
        headers=HEADERS,
    )

    resp = await client.get(
        f"/rooms/{room_id}/threads/{root_id}", headers=HEADERS,
    )
    assert resp.status_code == 200
    contents = [m.get("content") for m in resp.json()["messages"]]
    assert "in-thread" in contents
    assert "stranger to the thread" not in contents


# ---------------------------------------------------------------------------
# 6. Mixed threaded + non-threaded coexist in history
# ---------------------------------------------------------------------------


async def test_mixed_history_threaded_and_orphan(
    client: AsyncClient, room_id: str,
):
    root = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "root"},
        headers=HEADERS,
    )
    root_id = root.json()["id"]
    await client.post(
        f"/rooms/{room_id}/messages",
        json={
            "from_name": "bob", "content": "child",
            "thread_root_id": root_id,
        },
        headers=HEADERS,
    )
    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "carol", "content": "orphan"},
        headers=HEADERS,
    )

    history = await client.get(
        f"/rooms/{room_id}/history", headers=HEADERS,
    )
    assert history.status_code == 200
    contents = [m.get("content") for m in history.json()]
    assert {"root", "child", "orphan"}.issubset(set(contents))
