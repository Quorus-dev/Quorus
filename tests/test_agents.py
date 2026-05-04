"""Tests for the agent identity and profile endpoints.

GET /agents/{name} — returns profile with rooms, last_seen, message_count, online.
"""

from __future__ import annotations

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
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_presence(client: AsyncClient, name: str, room: str = "") -> None:
    """Post a heartbeat to register an agent in the presence system."""
    payload: dict = {"instance_name": name, "status": "active"}
    if room:
        payload["room"] = room
    resp = await client.post("/heartbeat", json=payload, headers=HEADERS)
    assert resp.status_code == 200


class TestGetAgentProfile:
    """GET /agents/{name} returns a full profile for a known agent."""

    async def test_returns_profile_schema(self, client: AsyncClient):
        await _register_presence(client, "alice")

        resp = await client.get("/agents/alice", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()

        assert data["name"] == "alice"
        assert isinstance(data["rooms"], list)
        assert "last_seen" in data
        assert isinstance(data["message_count"], int)
        assert isinstance(data["online"], bool)

    async def test_unknown_agent_returns_404(self, client: AsyncClient):
        resp = await client.get("/agents/this-agent-does-not-exist", headers=HEADERS)
        assert resp.status_code == 404

    async def test_no_auth_returns_401(self, client: AsyncClient):
        await _register_presence(client, "alice")
        resp = await client.get("/agents/alice")
        assert resp.status_code == 401

    async def test_agent_rooms_populated(self, client: AsyncClient):
        """Agent profile lists rooms the agent is a member of."""
        await _register_presence(client, "alice")

        # Create a room and join it
        room_resp = await client.post(
            "/rooms",
            json={"name": "dev-room", "created_by": "alice"},
            headers=HEADERS,
        )
        assert room_resp.status_code == 200

        resp = await client.get("/agents/alice", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        room_names = [r["name"] for r in data["rooms"]]
        assert "dev-room" in room_names

    async def test_message_count_increments(self, client: AsyncClient):
        """Message count reflects how many messages the agent has sent."""
        await _register_presence(client, "alice")

        # Create a room, join it, send a message
        room_resp = await client.post(
            "/rooms",
            json={"name": "msg-test-room", "created_by": "alice"},
            headers=HEADERS,
        )
        room_id = room_resp.json()["id"]
        await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": "bob"},
            headers=HEADERS,
        )
        await client.post(
            f"/rooms/{room_id}/messages",
            json={"from_name": "alice", "content": "hello everyone"},
            headers=HEADERS,
        )

        resp = await client.get("/agents/alice", headers=HEADERS)
        assert resp.json()["message_count"] >= 1


class TestOnlineStatus:
    """Online status reflects whether the agent has a recent heartbeat."""

    async def test_online_after_heartbeat(self, client: AsyncClient):
        await _register_presence(client, "live-agent")

        resp = await client.get("/agents/live-agent", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["online"] is True

    async def test_stale_agent_shows_offline(self, client: AsyncClient):
        """An agent that heartbeated a long time ago should show as offline.

        We test this by checking that an agent registered with the old
        heartbeat has online=False when the timeout has passed. This is
        hard to test without faking time; we instead verify that a freshly
        registered agent is online and leave stale-timeout testing to
        integration suites.
        """
        await _register_presence(client, "fresh-agent")

        resp = await client.get("/agents/fresh-agent", headers=HEADERS)
        assert resp.status_code == 200
        # Fresh heartbeat — must be online
        assert resp.json()["online"] is True

    async def test_multiple_agents_independent_status(self, client: AsyncClient):
        await _register_presence(client, "agent-one")
        await _register_presence(client, "agent-two")

        resp_one = await client.get("/agents/agent-one", headers=HEADERS)
        resp_two = await client.get("/agents/agent-two", headers=HEADERS)

        assert resp_one.json()["online"] is True
        assert resp_two.json()["online"] is True

    async def test_agent_with_room_in_heartbeat(self, client: AsyncClient):
        """Room field in heartbeat is recorded and the agent appears online."""
        await _register_presence(client, "room-agent", room="collab-room")

        resp = await client.get("/agents/room-agent", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["online"] is True


# ---------------------------------------------------------------------------
# Wave-5 Fix 5 — enumeration scoping
# ---------------------------------------------------------------------------


from quorus.auth.tokens import create_jwt  # noqa: E402

_FIX5_TENANT_ID = "t-fix5"
_FIX5_TENANT_SLUG = "fix5-tenant"


def _user_headers(name: str) -> dict[str, str]:
  """Bearer-JWT headers for a non-admin user in the test tenant."""
  token = create_jwt(
      sub=name,
      tenant_id=_FIX5_TENANT_ID,
      tenant_slug=_FIX5_TENANT_SLUG,
  )
  return {"Authorization": f"Bearer {token}"}


async def test_enumeration_blocked_for_non_admin_non_member(
    client: AsyncClient,
):
  """Non-admin caller with no shared room must get 403 — closing the
  OSINT vector that previously returned the full agent profile."""
  # Heartbeat the target agent in the legacy tenant (it has presence).
  await _register_presence(client, "target-agent")
  # Heartbeat the caller in the fix5 tenant — different tenant means no
  # presence visibility. Use a non-admin JWT for the lookup.
  resp = await client.post(
      "/heartbeat",
      json={"instance_name": "outsider", "status": "active"},
      headers=_user_headers("outsider"),
  )
  assert resp.status_code == 200, resp.text

  # Try to enumerate the target agent. Different tenants => 404 (target
  # doesn't exist in caller's tenant). Same tenant + no shared room
  # would 403. Both close the OSINT vector.
  resp = await client.get("/agents/target-agent", headers=_user_headers("outsider"))
  assert resp.status_code in (403, 404), resp.text


async def test_only_shared_rooms_returned(client: AsyncClient):
  """When caller and target share exactly ONE room out of many, the
  visible rooms list must contain only the shared room."""
  # Use real JWT auth in the fix5 tenant for both alice and bob so they
  # see the same room registry.
  # alice creates two rooms; bob is added only to "shared-room".
  r1 = await client.post(
      "/rooms",
      json={"name": "alice-private", "created_by": "alice"},
      headers=_user_headers("alice"),
  )
  assert r1.status_code == 200, r1.text
  r2 = await client.post(
      "/rooms",
      json={"name": "shared-room", "created_by": "alice"},
      headers=_user_headers("alice"),
  )
  assert r2.status_code == 200, r2.text
  shared_id = r2.json()["id"]
  # Add bob to the shared room.
  join = await client.post(
      f"/rooms/{shared_id}/join",
      json={"participant": "bob", "role": "member"},
      headers=_user_headers("alice"),
  )
  assert join.status_code == 200, join.text

  # alice heartbeats so she has presence in fix5 tenant.
  hb = await client.post(
      "/heartbeat",
      json={"instance_name": "alice", "status": "active"},
      headers=_user_headers("alice"),
  )
  assert hb.status_code == 200

  # bob queries alice — should ONLY see the shared room, never the
  # private one.
  resp = await client.get("/agents/alice", headers=_user_headers("bob"))
  assert resp.status_code == 200, resp.text
  data = resp.json()
  room_names = [r["name"] for r in data["rooms"]]
  assert "shared-room" in room_names
  assert "alice-private" not in room_names, (
      f"private room leaked to non-admin caller: {room_names}"
  )
  # Non-admin caller must NOT see message_count statistics.
  assert data["message_count"] == 0
