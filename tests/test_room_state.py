"""Tests for Primitive A (GET /rooms/{room}/state) and
Primitive B (POST/DELETE /rooms/{room}/lock — distributed mutex).
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from murmur.relay import _reset_state, app

HEADERS = {"Authorization": "Bearer test-secret"}


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


@pytest.fixture
async def room_id(client: AsyncClient) -> str:
    """Create a test room and return its id."""
    resp = await client.post(
        "/rooms",
        json={"name": "state-test-room", "created_by": "agent-alpha"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Primitive A — Shared State Matrix
# ---------------------------------------------------------------------------


class TestGetRoomState:
    """GET /rooms/{room}/state returns the shared state matrix."""

    async def test_returns_correct_schema(self, client: AsyncClient, room_id: str):
        resp = await client.get(f"/rooms/{room_id}/state", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()

        assert data["room_id"] == room_id
        assert "snapshot_at" in data
        assert data["schema_version"] == "1.0"
        assert "active_goal" in data
        assert isinstance(data["claimed_tasks"], list)
        assert isinstance(data["locked_files"], dict)
        assert isinstance(data["resolved_decisions"], list)
        assert isinstance(data["active_agents"], list)
        assert isinstance(data["message_count"], int)
        assert "last_activity" in data

    async def test_state_by_room_name(self, client: AsyncClient, room_id: str):
        """Room state is also resolvable by room name."""
        resp = await client.get("/rooms/state-test-room/state", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["room_id"] == room_id

    async def test_unknown_room_returns_404(self, client: AsyncClient):
        resp = await client.get("/rooms/no-such-room-xyz/state", headers=HEADERS)
        assert resp.status_code == 404

    async def test_initial_state_is_empty(self, client: AsyncClient, room_id: str):
        resp = await client.get(f"/rooms/{room_id}/state", headers=HEADERS)
        data = resp.json()
        assert data["claimed_tasks"] == []
        assert data["locked_files"] == {}
        assert data["active_goal"] is None
        assert data["message_count"] == 0

    async def test_locked_file_appears_in_state(self, client: AsyncClient, room_id: str):
        await client.post(
            f"/rooms/{room_id}/lock",
            json={
                "file_path": "src/auth.py",
                "claimed_by": "agent-alpha",
                "ttl_seconds": 300,
            },
            headers=HEADERS,
        )

        resp = await client.get(f"/rooms/{room_id}/state", headers=HEADERS)
        data = resp.json()
        assert "src/auth.py" in data["locked_files"]

    async def test_message_count_increments(self, client: AsyncClient, room_id: str):
        await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": "agent-beta"},
            headers=HEADERS,
        )
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "agent-alpha", "content": "hello room"},
            headers=HEADERS,
        )

        resp = await client.get(f"/rooms/{room_id}/state", headers=HEADERS)
        assert resp.json()["message_count"] >= 1

    async def test_no_auth_returns_401(self, client: AsyncClient, room_id: str):
        resp = await client.get(f"/rooms/{room_id}/state")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Primitive B — Distributed Mutex (file-path lock)
# ---------------------------------------------------------------------------


class TestClaimLock:
    """POST /rooms/{room}/lock — acquire an optimistic file lock."""

    async def test_grants_lock_with_correct_fields(self, client: AsyncClient, room_id: str):
        resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={
                "file_path": "src/main.py",
                "claimed_by": "agent-alpha",
                "ttl_seconds": 300,
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["locked"] is False
        assert "lock_token" in data
        assert "expires_at" in data
        assert len(data["lock_token"]) > 0

    async def test_second_claim_returns_locked_true(self, client: AsyncClient, room_id: str):
        # First agent claims the file
        await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/main.py", "claimed_by": "agent-alpha", "ttl_seconds": 300},
            headers=HEADERS,
        )

        # Second agent tries the same file
        resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/main.py", "claimed_by": "agent-beta", "ttl_seconds": 300},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["locked"] is True
        assert data["held_by"] == "agent-alpha"
        assert "expires_at" in data

    async def test_different_paths_are_independent(self, client: AsyncClient, room_id: str):
        """Two agents can each lock different files without conflict."""
        resp_a = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/auth.py", "claimed_by": "agent-alpha", "ttl_seconds": 300},
            headers=HEADERS,
        )
        resp_b = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/db.py", "claimed_by": "agent-beta", "ttl_seconds": 300},
            headers=HEADERS,
        )
        assert resp_a.json()["locked"] is False
        assert resp_b.json()["locked"] is False

    async def test_lock_appears_in_state(self, client: AsyncClient, room_id: str):
        await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/api.py", "claimed_by": "agent-alpha", "ttl_seconds": 300},
            headers=HEADERS,
        )

        state = (await client.get(f"/rooms/{room_id}/state", headers=HEADERS)).json()
        assert "src/api.py" in state["locked_files"]
        assert state["locked_files"]["src/api.py"]["held_by"] == "agent-alpha"

    async def test_unknown_room_returns_404(self, client: AsyncClient):
        resp = await client.post(
            "/rooms/no-such-room/lock",
            json={"file_path": "x.py", "claimed_by": "agent", "ttl_seconds": 60},
            headers=HEADERS,
        )
        assert resp.status_code == 404

    async def test_no_auth_returns_401(self, client: AsyncClient, room_id: str):
        resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "x.py", "claimed_by": "agent", "ttl_seconds": 60},
        )
        assert resp.status_code == 401


class TestReleaseLock:
    """DELETE /rooms/{room}/lock/{path} — release a file lock."""

    async def _acquire(self, client: AsyncClient, room_id: str, path: str) -> str:
        resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": path, "claimed_by": "agent-alpha", "ttl_seconds": 300},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        return resp.json()["lock_token"]

    async def test_correct_token_releases_lock(self, client: AsyncClient, room_id: str):
        token = await self._acquire(client, room_id, "src/model.py")

        resp = await client.request(
            "DELETE",
            f"/rooms/{room_id}/lock/src/model.py",
            json={"lock_token": token},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["released"] is True
        assert data["file_path"] == "src/model.py"

    async def test_wrong_token_returns_403(self, client: AsyncClient, room_id: str):
        await self._acquire(client, room_id, "src/model.py")

        resp = await client.request(
            "DELETE",
            f"/rooms/{room_id}/lock/src/model.py",
            json={"lock_token": "not-the-right-token"},
            headers=HEADERS,
        )
        assert resp.status_code == 403

    async def test_release_nonexistent_lock_returns_404(
        self, client: AsyncClient, room_id: str
    ):
        resp = await client.request(
            "DELETE",
            f"/rooms/{room_id}/lock/src/no-lock-here.py",
            json={"lock_token": "any-token"},
            headers=HEADERS,
        )
        assert resp.status_code == 404

    async def test_released_lock_disappears_from_state(
        self, client: AsyncClient, room_id: str
    ):
        token = await self._acquire(client, room_id, "src/router.py")

        # Confirm it's in state
        state_before = (
            await client.get(f"/rooms/{room_id}/state", headers=HEADERS)
        ).json()
        assert "src/router.py" in state_before["locked_files"]

        # Release it
        await client.request(
            "DELETE",
            f"/rooms/{room_id}/lock/src/router.py",
            json={"lock_token": token},
            headers=HEADERS,
        )

        # Confirm it's gone
        state_after = (
            await client.get(f"/rooms/{room_id}/state", headers=HEADERS)
        ).json()
        assert "src/router.py" not in state_after["locked_files"]

    async def test_no_auth_returns_401(self, client: AsyncClient, room_id: str):
        resp = await client.request(
            "DELETE",
            f"/rooms/{room_id}/lock/src/x.py",
            json={"lock_token": "tok"},
        )
        assert resp.status_code == 401


class TestLockTTL:
    """Lock TTL expiry: lock should disappear after ttl_seconds elapsed."""

    async def test_expired_lock_not_present_in_state(
        self, client: AsyncClient, room_id: str
    ):
        """Create a lock with ttl=1, wait 2 seconds, verify it's gone from state."""
        resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/expire.py", "claimed_by": "agent-alpha", "ttl_seconds": 1},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["locked"] is False

        # Wait for TTL to expire
        await asyncio.sleep(2)

        state = (await client.get(f"/rooms/{room_id}/state", headers=HEADERS)).json()
        assert "src/expire.py" not in state["locked_files"]

    async def test_expired_lock_can_be_re_acquired(
        self, client: AsyncClient, room_id: str
    ):
        """After TTL expiry, another agent can claim the same file."""
        await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/shared.py", "claimed_by": "agent-alpha", "ttl_seconds": 1},
            headers=HEADERS,
        )

        await asyncio.sleep(2)

        resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/shared.py", "claimed_by": "agent-beta", "ttl_seconds": 300},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["locked"] is False
        assert "lock_token" in resp.json()


class TestLockSSEBroadcast:
    """Verify LOCK_ACQUIRED SSE events are pushed when a lock is granted."""

    async def test_lock_acquired_sse_event_is_broadcast(
        self, client: AsyncClient, room_id: str
    ):
        sse_svc = app.state.sse_service

        # Add a member to the room so they receive the broadcast
        await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": "watcher"},
            headers=HEADERS,
        )

        # Register an SSE queue for the watcher
        q = sse_svc.register_queue("_legacy", "watcher")

        # Claim a lock — should trigger LOCK_ACQUIRED broadcast
        resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/target.py", "claimed_by": "agent-alpha", "ttl_seconds": 300},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["locked"] is False

        # SSE queue should have received the event
        msg = await asyncio.wait_for(q.get(), timeout=2)
        assert msg["message_type"] == "LOCK_ACQUIRED"

        sse_svc.unregister_queue("_legacy", "watcher", q)

    async def test_lock_released_sse_event_is_broadcast(
        self, client: AsyncClient, room_id: str
    ):
        sse_svc = app.state.sse_service

        await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": "watcher"},
            headers=HEADERS,
        )
        q = sse_svc.register_queue("_legacy", "watcher")

        # Acquire
        lock_resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/target.py", "claimed_by": "agent-alpha", "ttl_seconds": 300},
            headers=HEADERS,
        )
        token = lock_resp.json()["lock_token"]

        # Drain the LOCK_ACQUIRED event
        await asyncio.wait_for(q.get(), timeout=2)

        # Release
        await client.request(
            "DELETE",
            f"/rooms/{room_id}/lock/src/target.py",
            json={"lock_token": token},
            headers=HEADERS,
        )

        # LOCK_RELEASED should appear in the queue
        msg = await asyncio.wait_for(q.get(), timeout=2)
        assert msg["message_type"] == "LOCK_RELEASED"

        sse_svc.unregister_queue("_legacy", "watcher", q)
