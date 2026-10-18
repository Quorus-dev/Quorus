"""Tests for the persistent-memory primitive — Plan v8 Phase 1 C.

Service unit tests + route integration covering set/get round-trip,
size + entry caps, visibility-gated reads, and GDPR-compliant delete.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app
from quorus.services.persistent_memory_svc import (
    MAX_ENTRIES,
    MAX_VALUE_BYTES,
    EntryCapError,
    MemoryError_,
    PersistentMemorySvc,
    ValueTooLargeError,
)

_TEST_TENANT_ID = "t-mem"
_TEST_TENANT_SLUG = "mem-test"


def _user_headers(name: str, role: str = "user") -> dict[str, str]:
    token = create_jwt(
        sub=name,
        tenant_id=_TEST_TENANT_ID,
        tenant_slug=_TEST_TENANT_SLUG,
        role=role,
    )
    return {"Authorization": f"Bearer {token}"}


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
        json={"name": "mem-room", "created_by": "alice"},
        headers=_user_headers("alice"),
    )
    assert resp.status_code == 200
    rid = resp.json()["id"]
    join = await client.post(
        f"/rooms/{rid}/join",
        json={"participant": "bob", "role": "member"},
        headers=_user_headers("alice"),
    )
    assert join.status_code == 200
    return rid


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


class TestPersistentMemorySvc:
    async def test_set_then_get_round_trip(self):
        svc = PersistentMemorySvc()
        await svc.set(
            "tid", "alice", "rid", "key1", {"hello": "world"},
        )
        out = await svc.get("tid", "alice", "rid", "key1")
        assert out is not None
        assert out["value"] == {"hello": "world"}

    async def test_value_size_cap_enforced(self):
        svc = PersistentMemorySvc()
        oversized = "x" * (MAX_VALUE_BYTES + 100)
        with pytest.raises(ValueTooLargeError):
            await svc.set("tid", "alice", "rid", "k", oversized)

    async def test_entry_cap_enforced(self):
        # Don't actually write 1024 — patch a smaller cap via service
        # internals would couple. Instead use a fresh svc with the
        # documented cap, but only assert raise behaviour at boundary.
        # Cheaper: write to MAX_ENTRIES-1 and verify the next throws
        # — but 1024 writes is fine, fast enough at <50ms.
        svc = PersistentMemorySvc()
        for i in range(MAX_ENTRIES):
            await svc.set("tid", "alice", "rid", f"k{i}", i)
        with pytest.raises(EntryCapError):
            await svc.set("tid", "alice", "rid", "overflow", 1)

    async def test_invalid_visibility_rejected(self):
        svc = PersistentMemorySvc()
        with pytest.raises(MemoryError_):
            await svc.set(
                "tid", "alice", "rid", "k", "v", visibility="cosmic",
            )

    async def test_delete_actually_deletes(self):
        svc = PersistentMemorySvc()
        await svc.set("tid", "alice", "rid", "k", "v")
        assert await svc.delete("tid", "alice", "rid", "k") is True
        assert await svc.get("tid", "alice", "rid", "k") is None

    async def test_list_visibility_filter(self):
        svc = PersistentMemorySvc()
        await svc.set("tid", "a", "r", "private-key", "pv")
        await svc.set(
            "tid", "a", "r", "room-key", "rv", visibility="room",
        )
        only_room = await svc.list(
            "tid", "a", "r", visibility_filter="room",
        )
        assert [e["key"] for e in only_room] == ["room-key"]


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


class TestPersistentMemoryRoutes:
    async def test_set_then_get(
        self, client: AsyncClient, room_id: str,
    ):
        resp = await client.put(
            f"/v1/memory/alice/{room_id}/k1",
            json={"value": {"plan": "ship"}, "visibility": "private"},
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 200, resp.text
        get_resp = await client.get(
            f"/v1/memory/alice/{room_id}/k1",
            headers=_user_headers("alice"),
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["entry"]["value"] == {"plan": "ship"}

    async def test_cannot_write_for_another_participant(
        self, client: AsyncClient, room_id: str,
    ):
        # Bob is a member but cannot write Alice's memory.
        resp = await client.put(
            f"/v1/memory/alice/{room_id}/k",
            json={"value": "x", "visibility": "private"},
            headers=_user_headers("bob"),
        )
        assert resp.status_code == 403

    async def test_private_visibility_blocks_other_member(
        self, client: AsyncClient, room_id: str,
    ):
        await client.put(
            f"/v1/memory/alice/{room_id}/secret",
            json={"value": "shh", "visibility": "private"},
            headers=_user_headers("alice"),
        )
        # Bob is a room member but cannot read alice's PRIVATE key.
        resp = await client.get(
            f"/v1/memory/alice/{room_id}/secret",
            headers=_user_headers("bob"),
        )
        assert resp.status_code == 404

    async def test_room_visibility_allows_room_member(
        self, client: AsyncClient, room_id: str,
    ):
        await client.put(
            f"/v1/memory/alice/{room_id}/shared",
            json={"value": "go", "visibility": "room"},
            headers=_user_headers("alice"),
        )
        resp = await client.get(
            f"/v1/memory/alice/{room_id}/shared",
            headers=_user_headers("bob"),
        )
        assert resp.status_code == 200
        assert resp.json()["entry"]["value"] == "go"

    async def test_delete_actually_deletes(
        self, client: AsyncClient, room_id: str,
    ):
        await client.put(
            f"/v1/memory/alice/{room_id}/del-me",
            json={"value": "bye", "visibility": "private"},
            headers=_user_headers("alice"),
        )
        del_resp = await client.delete(
            f"/v1/memory/alice/{room_id}/del-me",
            headers=_user_headers("alice"),
        )
        assert del_resp.status_code == 200
        get_resp = await client.get(
            f"/v1/memory/alice/{room_id}/del-me",
            headers=_user_headers("alice"),
        )
        assert get_resp.status_code == 404

    async def test_oversized_value_413(
        self, client: AsyncClient, room_id: str,
    ):
        oversized = "x" * (MAX_VALUE_BYTES + 100)
        resp = await client.put(
            f"/v1/memory/alice/{room_id}/big",
            json={"value": oversized, "visibility": "private"},
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 413

    async def test_non_member_cannot_read_room_visibility(
        self, client: AsyncClient, room_id: str,
    ):
        # Alice writes a 'room'-visibility entry; Carol (non-member) gets 403
        # at the membership gate before visibility check.
        await client.put(
            f"/v1/memory/alice/{room_id}/shared",
            json={"value": "x", "visibility": "room"},
            headers=_user_headers("alice"),
        )
        resp = await client.get(
            f"/v1/memory/alice/{room_id}/shared",
            headers=_user_headers("carol"),
        )
        assert resp.status_code == 403

    async def test_list_returns_visible_entries(
        self, client: AsyncClient, room_id: str,
    ):
        await client.put(
            f"/v1/memory/alice/{room_id}/private-k",
            json={"value": "p", "visibility": "private"},
            headers=_user_headers("alice"),
        )
        await client.put(
            f"/v1/memory/alice/{room_id}/room-k",
            json={"value": "r", "visibility": "room"},
            headers=_user_headers("alice"),
        )
        # Bob sees only the room-visible one.
        resp = await client.get(
            f"/v1/memory/alice/{room_id}",
            headers=_user_headers("bob"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["entries"][0]["key"] == "room-k"


# ---------------------------------------------------------------------------
# Shared-secret relays (the default local setup) — 0.5.0 sweep, A3
# ---------------------------------------------------------------------------

_LEGACY_HEADERS = {"Authorization": "Bearer test-secret"}


class TestSharedSecretRelay:
    """``quorus init --secret`` gives the MCP server the shared secret and no
    JWT. memory_set/memory_get answered 403 "legacy auth not permitted" while
    every other tool worked. The secret is the relay's admin everywhere else,
    so here it acts as the participant named in the URL."""

    @pytest.fixture
    async def legacy_room(self, client: AsyncClient) -> str:
        resp = await client.post(
            "/rooms", json={"name": "local-room", "created_by": "arav"},
            headers=_LEGACY_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    async def test_set_get_list_delete_round_trip(
        self, client: AsyncClient, legacy_room: str,
    ):
        put = await client.put(
            f"/v1/memory/arav-claude/{legacy_room}/plan",
            json={"value": {"step": 1}, "visibility": "private"},
            headers=_LEGACY_HEADERS,
        )
        assert put.status_code == 200, put.text
        assert put.json()["entry"]["owner"] == "arav-claude"

        got = await client.get(
            f"/v1/memory/arav-claude/{legacy_room}/plan", headers=_LEGACY_HEADERS,
        )
        assert got.status_code == 200, got.text
        assert got.json()["entry"]["value"] == {"step": 1}

        listed = await client.get(
            f"/v1/memory/arav-claude/{legacy_room}", headers=_LEGACY_HEADERS,
        )
        assert listed.status_code == 200
        assert [e["key"] for e in listed.json()["entries"]] == ["plan"]

        gone = await client.delete(
            f"/v1/memory/arav-claude/{legacy_room}/plan", headers=_LEGACY_HEADERS,
        )
        assert gone.status_code == 200
        assert (await client.get(
            f"/v1/memory/arav-claude/{legacy_room}/plan", headers=_LEGACY_HEADERS,
        )).status_code == 404

    async def test_writes_are_rate_limited_per_participant(
        self, client: AsyncClient, legacy_room: str,
    ):
        """The bucket used to be keyed on ``auth.sub`` (None for the secret),
        which would have pooled every agent on the relay into one limit."""
        for name in ("arav-claude", "arav-codex"):
            for i in range(60):
                resp = await client.put(
                    f"/v1/memory/{name}/{legacy_room}/k{i}",
                    json={"value": i}, headers=_LEGACY_HEADERS,
                )
                assert resp.status_code == 200, (name, i, resp.text)
        resp = await client.put(
            f"/v1/memory/arav-claude/{legacy_room}/one-more",
            json={"value": 1}, headers=_LEGACY_HEADERS,
        )
        assert resp.status_code == 429

    async def test_jwt_without_subject_is_still_refused(
        self, client: AsyncClient, legacy_room: str,
    ):
        token = create_jwt(sub="", tenant_id="_legacy", tenant_slug="_legacy")
        resp = await client.put(
            f"/v1/memory/arav-claude/{legacy_room}/k",
            json={"value": 1}, headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (401, 403)

    async def test_mcp_memory_tools_work_over_the_shared_secret(
        self, client: AsyncClient, legacy_room: str, monkeypatch,
    ):
        """End to end: the MCP tools exactly as a `quorus init --secret` agent
        sends them (shared secret as bearer, no API key) against the relay app."""
        import quorus_mcp.server as srv
        from quorus_mcp import phase1_tools

        monkeypatch.setattr(srv, "RELAY_URL", "http://test")
        monkeypatch.setattr(srv, "RELAY_SECRET", "test-secret")
        monkeypatch.setattr(srv, "API_KEY", "")
        monkeypatch.setattr(srv, "INSTANCE_NAME", "arav-claude")
        monkeypatch.setattr(srv, "_cached_jwt", None)
        monkeypatch.setattr(srv, "_get_http_client", lambda: client)

        set_out = await phase1_tools.memory_set(legacy_room, "focus", "ship 0.5.1")
        assert set_out.startswith("OK: stored 'focus'"), set_out
        get_out = await phase1_tools.memory_get(legacy_room, "focus")
        assert "ship 0.5.1" in get_out and "Error" not in get_out, get_out
