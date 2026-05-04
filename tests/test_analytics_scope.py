"""S2 regression — /analytics per_participant must be scoped to caller.

Previously the endpoint returned per-participant {sent, received} for every
participant in the tenant. This test pins the scope guard: a non-admin /
non-legacy caller only sees participants who appear in their visible rooms.
"""
from __future__ import annotations

from typing import Any

import pytest

from quorus.routes import analytics as analytics_route


class _Auth:
    """Minimal AuthContext stub matching the attributes the route reads."""

    def __init__(
        self,
        *,
        sub: str | None = None,
        role: str | None = None,
        is_legacy: bool = False,
        tenant_id: str | None = "tenant-a",
    ):
        self.sub = sub
        self.role = role
        self.is_legacy = is_legacy
        self.tenant_id = tenant_id


class _StubAnalyticsSvc:
    async def get_stats(self, tid: str) -> dict[str, Any]:
        # Tenant has alice, bob, carol, dave — all sending and receiving.
        return {
            "total_sent": 4,
            "total_delivered": 4,
            "per_sender": {
                "alice": 1, "bob": 1, "carol": 1, "dave": 1,
            },
            "per_recipient": {
                "alice": 1, "bob": 1, "carol": 1, "dave": 1,
            },
            "hourly_volume": {},
        }


class _StubMessagesBackend:
    async def count_all(self, tid: str) -> int:
        return 0


class _StubBackends:
    def __init__(self):
        self.messages = _StubMessagesBackend()


class _StubRoomSvc:
    """Returns a single room for alice — alice + bob are members. carol /
    dave are in OTHER rooms in the same tenant that alice can't see.
    """

    async def list_for_member(self, tid: str, member: str) -> list[dict[str, Any]]:
        if member == "alice":
            return [{"id": "r1", "members": {"alice": {}, "bob": {}}}]
        return []


class _StubAppState:
    def __init__(self):
        self.analytics_service = _StubAnalyticsSvc()
        self.backends = _StubBackends()
        self.room_service = _StubRoomSvc()


class _StubApp:
    def __init__(self):
        self.state = _StubAppState()


class _StubRequest:
    def __init__(self):
        self.app = _StubApp()


@pytest.mark.asyncio
async def test_analytics_non_admin_only_sees_visible_rooms():
    """Non-admin caller's per_participant is restricted to their rooms."""
    request = _StubRequest()
    auth = _Auth(sub="alice", role="agent", is_legacy=False)

    resp = await analytics_route.get_analytics(request, auth)

    participants = resp["participants"]
    # Alice can see only alice + bob (her room mates) — NEVER carol/dave.
    assert set(participants.keys()) == {"alice", "bob"}
    assert participants["alice"]["sent"] == 1
    assert participants["bob"]["received"] == 1
    # The total counters are NOT scoped — they're aggregate per-tenant —
    # only the per-participant breakdown is restricted.
    assert resp["total_messages_sent"] == 4


@pytest.mark.asyncio
async def test_analytics_admin_sees_all_participants():
    """Admin role bypasses the scope guard — full visibility preserved."""
    request = _StubRequest()
    auth = _Auth(sub="admin", role="admin", is_legacy=False)

    resp = await analytics_route.get_analytics(request, auth)
    participants = resp["participants"]
    assert set(participants.keys()) == {"alice", "bob", "carol", "dave"}


@pytest.mark.asyncio
async def test_analytics_legacy_auth_sees_all_participants():
    """Legacy bearer auth (no tenant identity) keeps full visibility for
    backward compatibility — same as /v1/usage's contract.
    """
    request = _StubRequest()
    auth = _Auth(sub=None, role=None, is_legacy=True)

    resp = await analytics_route.get_analytics(request, auth)
    participants = resp["participants"]
    assert set(participants.keys()) == {"alice", "bob", "carol", "dave"}
