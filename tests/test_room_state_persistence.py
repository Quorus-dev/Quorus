import asyncio
import socket
from unittest.mock import patch
import pytest
from httpx import AsyncClient, ASGITransport
from quorus.relay import _save_to_file, _load_from_file, _reset_state, app

@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-secret"}

@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

@pytest.mark.asyncio
async def test_room_state_persistence(client: AsyncClient, auth_headers: dict, tmp_path):
    """Verify that room goal and locks survive a reload."""
    filepath = str(tmp_path / "messages.json")
    
    with patch("quorus.relay.MESSAGES_FILE", filepath):
        # 1. Create a room
        resp = await client.post(
            "/rooms",
            json={"name": "state-room", "created_by": "alice"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        room_id = resp.json()["id"]

        # 2. Set a goal
        resp = await client.patch(
            f"/rooms/{room_id}/state/goal",
            json={"goal": "Finish the sprint", "set_by": "alice"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # 3. Claim a lock
        resp = await client.post(
            f"/rooms/{room_id}/lock",
            json={"file_path": "src/main.py", "claimed_by": "alice"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["locked"] is False

        # 4. Save and reload
        _save_to_file()
        _reset_state()
        _load_from_file()

        # 5. Verify state survives
        resp = await client.get(f"/rooms/{room_id}/state", headers=auth_headers)
        assert resp.status_code == 200
        state = resp.json()
        assert state["active_goal"] == "Finish the sprint"
        assert "src/main.py" in state["locked_files"]
        assert state["locked_files"]["src/main.py"]["held_by"] == "alice"

@pytest.mark.asyncio
async def test_analytics_and_participants_persistence(client: AsyncClient, auth_headers: dict, tmp_path):
    """Verify that analytics and participants survive a reload."""
    filepath = str(tmp_path / "messages.json")
    
    with patch("quorus.relay.MESSAGES_FILE", filepath):
        # 1. Trigger some activity
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello"},
            headers=auth_headers,
        )
        
        # 2. Verify participants recorded
        resp = await client.get("/participants", headers=auth_headers)
        participants = resp.json()
        assert "alice" in participants
        assert "bob" in participants

        # 3. Save and reload
        _save_to_file()
        _reset_state()
        _load_from_file()

        # 4. Verify participants survive
        resp = await client.get("/participants", headers=auth_headers)
        assert "alice" in resp.json()
        assert "bob" in resp.json()

        # 5. Verify analytics survive (using admin metrics route)
        resp = await client.get("/admin/metrics", headers=auth_headers)
        assert resp.status_code == 200
        metrics = resp.json()
        assert metrics["messages"]["total_30d"] >= 1
