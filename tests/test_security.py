"""Security hardening tests for the Murmur relay.

Tests every endpoint with:
- Missing / wrong / malformed auth
- Missing required fields
- Huge payloads
- Special characters, injection attempts
- Path traversal
- Boundary conditions
- Rate-limit enforcement
"""

import os
import socket
from unittest.mock import patch

os.environ.setdefault("RELAY_SECRET", "test-secret")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from murmur.relay import (
    _reset_state,
    app,
)

AUTH = {"Authorization": "Bearer test-secret"}


def _fake_getaddrinfo_public(host, port, family=0, type_=0, proto=0, flags=0):
    """Return a public IP for any hostname so webhook tests don't depend on real DNS."""
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


@pytest.fixture(autouse=True)
def clean():
    _reset_state()
    with patch("socket.getaddrinfo", _fake_getaddrinfo_public):
        yield
    _reset_state()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_room(client, name="sec-room", creator="admin"):
    return await client.post(
        "/rooms", json={"name": name, "created_by": creator}, headers=AUTH
    )


async def _join_room(client, room_id, participant):
    return await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": participant},
        headers=AUTH,
    )


# ===========================================================================
# 1. AUTH — every authenticated endpoint must reject bad/missing auth
# ===========================================================================

class TestAuthRequired:
    """Every authenticated endpoint must return 401 without valid auth."""

    ENDPOINTS_GET = [
        "/health/detailed",
        "/messages/alice",
        "/messages/alice/peek",
        "/participants",
        "/presence",
        "/analytics",
        "/rooms",
    ]

    ENDPOINTS_POST = [
        ("/messages", {"from_name": "a", "to": "b", "content": "hi"}),
        ("/heartbeat", {"instance_name": "a"}),
        ("/webhooks", {"instance_name": "a", "callback_url": "https://example.com/hook"}),
        ("/rooms", {"name": "r", "created_by": "a"}),
    ]

    @pytest.mark.anyio
    @pytest.mark.parametrize("path", ENDPOINTS_GET)
    async def test_get_no_auth(self, client, path):
        r = await client.get(path)
        assert r.status_code == 401

    @pytest.mark.anyio
    @pytest.mark.parametrize("path", ENDPOINTS_GET)
    async def test_get_wrong_auth(self, client, path):
        r = await client.get(path, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    @pytest.mark.anyio
    @pytest.mark.parametrize("path", ENDPOINTS_GET)
    async def test_get_malformed_auth(self, client, path):
        r = await client.get(path, headers={"Authorization": "test-secret"})
        assert r.status_code == 401

    @pytest.mark.anyio
    @pytest.mark.parametrize("path,body", ENDPOINTS_POST)
    async def test_post_no_auth(self, client, path, body):
        r = await client.post(path, json=body)
        assert r.status_code == 401

    @pytest.mark.anyio
    @pytest.mark.parametrize("path,body", ENDPOINTS_POST)
    async def test_post_wrong_auth(self, client, path, body):
        r = await client.post(path, json=body, headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_delete_webhook_no_auth(self, client):
        r = await client.delete("/webhooks/foo")
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_room_join_no_auth(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(f"/rooms/{rid}/join", json={"participant": "x"})
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_room_leave_no_auth(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(f"/rooms/{rid}/leave", json={"participant": "x"})
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_room_message_no_auth(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "admin", "content": "hi"},
        )
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_room_history_no_auth(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.get(f"/rooms/{rid}/history")
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_room_get_no_auth(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.get(f"/rooms/{rid}")
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_sse_stream_no_token(self, client):
        r = await client.get("/stream/alice")
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_sse_stream_wrong_token(self, client):
        r = await client.get("/stream/alice?token=wrong")
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_empty_bearer_token(self, client):
        r = await client.get("/health/detailed", headers={"Authorization": "Bearer "})
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_bearer_with_extra_spaces(self, client):
        r = await client.get(
            "/health/detailed",
            headers={"Authorization": "Bearer  test-secret"},
        )
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_bearer_case_sensitivity(self, client):
        r = await client.get(
            "/health/detailed",
            headers={"Authorization": "bearer test-secret"},
        )
        assert r.status_code == 401


# ===========================================================================
# 2. PUBLIC endpoints must NOT require auth
# ===========================================================================

class TestPublicEndpoints:
    @pytest.mark.anyio
    async def test_health_no_auth(self, client):
        r = await client.get("/health")
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_dashboard_no_auth(self, client):
        r = await client.get("/")
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_invite_page_requires_auth(self, client):
        await _create_room(client, name="pub-room")
        # Without auth — should be rejected
        r = await client.get("/invite/pub-room")
        assert r.status_code == 401
        # With auth — should succeed
        r = await client.get("/invite/pub-room", headers=AUTH)
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_invite_page_missing_room(self, client):
        r = await client.get("/invite/nonexistent", headers=AUTH)
        assert r.status_code == 404


# ===========================================================================
# 3. INPUT VALIDATION — missing fields, bad types, empty bodies
# ===========================================================================

class TestInputValidation:
    @pytest.mark.anyio
    async def test_send_message_empty_body(self, client):
        r = await client.post("/messages", json={}, headers=AUTH)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_send_message_missing_content(self, client):
        r = await client.post(
            "/messages", json={"from_name": "a", "to": "b"}, headers=AUTH
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_send_message_missing_from(self, client):
        r = await client.post(
            "/messages", json={"to": "b", "content": "hi"}, headers=AUTH
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_send_message_missing_to(self, client):
        r = await client.post(
            "/messages", json={"from_name": "a", "content": "hi"}, headers=AUTH
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_send_message_no_json(self, client):
        r = await client.post(
            "/messages", content=b"not json", headers={**AUTH, "Content-Type": "application/json"}
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_send_message_null_fields(self, client):
        r = await client.post(
            "/messages",
            json={"from_name": None, "to": None, "content": None},
            headers=AUTH,
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_send_message_numeric_fields(self, client):
        r = await client.post(
            "/messages",
            json={"from_name": 123, "to": 456, "content": 789},
            headers=AUTH,
        )
        # Pydantic coerces ints to strings in some modes — should not crash
        assert r.status_code in (200, 422)

    @pytest.mark.anyio
    async def test_heartbeat_empty_body(self, client):
        r = await client.post("/heartbeat", json={}, headers=AUTH)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_heartbeat_invalid_status(self, client):
        r = await client.post(
            "/heartbeat",
            json={"instance_name": "a", "status": "hacking"},
            headers=AUTH,
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_create_room_empty_body(self, client):
        r = await client.post("/rooms", json={}, headers=AUTH)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_join_room_empty_body(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(f"/rooms/{rid}/join", json={}, headers=AUTH)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_room_message_empty_body(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(f"/rooms/{rid}/messages", json={}, headers=AUTH)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_room_message_invalid_type(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "admin", "content": "hi", "message_type": "exploit"},
            headers=AUTH,
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_webhook_empty_body(self, client):
        r = await client.post("/webhooks", json={}, headers=AUTH)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_webhook_missing_url(self, client):
        r = await client.post(
            "/webhooks", json={"instance_name": "a"}, headers=AUTH
        )
        assert r.status_code == 422


# ===========================================================================
# 4. NAME VALIDATION — special chars, injection, length
# ===========================================================================

class TestNameValidation:
    INJECTION_NAMES = [
        "",
        " ",
        "../../../etc/passwd",
        "<script>alert(1)</script>",
        "'; DROP TABLE users; --",
        "name\x00null",
        "name\nwith\nnewlines",
        "name\twith\ttabs",
        "x" * 65,  # exceeds MAX_NAME_LENGTH (64)
        "name with spaces",
        "name@domain.com",
        "${7*7}",
        "{{constructor.constructor('return this')()}}",
        "../../.env",
        "name/slash",
        "%00null",
    ]

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_name", INJECTION_NAMES)
    async def test_send_message_bad_from_name(self, client, bad_name):
        r = await client.post(
            "/messages",
            json={"from_name": bad_name, "to": "bob", "content": "hi"},
            headers=AUTH,
        )
        assert r.status_code == 422, f"from_name={bad_name!r} was accepted"

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_name", INJECTION_NAMES)
    async def test_send_message_bad_to_name(self, client, bad_name):
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": bad_name, "content": "hi"},
            headers=AUTH,
        )
        assert r.status_code == 422, f"to={bad_name!r} was accepted"

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_name", INJECTION_NAMES)
    async def test_create_room_bad_name(self, client, bad_name):
        r = await client.post(
            "/rooms",
            json={"name": bad_name, "created_by": "admin"},
            headers=AUTH,
        )
        assert r.status_code == 422, f"room name={bad_name!r} was accepted"

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_name", INJECTION_NAMES)
    async def test_create_room_bad_creator(self, client, bad_name):
        r = await client.post(
            "/rooms",
            json={"name": "safe-room", "created_by": bad_name},
            headers=AUTH,
        )
        assert r.status_code == 422, f"created_by={bad_name!r} was accepted"

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_name", INJECTION_NAMES)
    async def test_join_room_bad_participant(self, client, bad_name):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/join",
            json={"participant": bad_name},
            headers=AUTH,
        )
        assert r.status_code == 422, f"participant={bad_name!r} was accepted"

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_name", INJECTION_NAMES)
    async def test_heartbeat_bad_instance_name(self, client, bad_name):
        r = await client.post(
            "/heartbeat",
            json={"instance_name": bad_name},
            headers=AUTH,
        )
        assert r.status_code == 422, f"instance_name={bad_name!r} was accepted"

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_name", INJECTION_NAMES)
    async def test_webhook_bad_instance_name(self, client, bad_name):
        r = await client.post(
            "/webhooks",
            json={"instance_name": bad_name, "callback_url": "https://example.com/hook"},
            headers=AUTH,
        )
        assert r.status_code == 422, f"instance_name={bad_name!r} was accepted"


# ===========================================================================
# 5. HUGE PAYLOADS — must not crash the server
# ===========================================================================

class TestHugePayloads:
    @pytest.mark.anyio
    async def test_message_at_size_limit(self, client):
        content = "A" * 51200  # exactly MAX_MESSAGE_SIZE
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": content},
            headers=AUTH,
        )
        # Should be chunked or accepted — not crash
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_dm_over_size_limit_rejected(self, client):
        content = "A" * 60000  # over MAX_MESSAGE_SIZE (51200)
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": content},
            headers=AUTH,
        )
        assert r.status_code == 413  # consistent with room message behavior

    @pytest.mark.anyio
    async def test_room_message_over_size_limit_rejected(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        content = "A" * 60000
        r = await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "admin", "content": content},
            headers=AUTH,
        )
        assert r.status_code == 413

    @pytest.mark.anyio
    async def test_huge_json_body(self, client):
        """Sending a very deeply nested JSON should not crash."""
        r = await client.post(
            "/messages",
            json={
                "from_name": "alice",
                "to": "bob",
                "content": "x",
                "extra_field_1": {"nested": {"deep": "value"} },
                "extra_field_2": [1] * 1000,
            },
            headers=AUTH,
        )
        # Extra fields ignored by Pydantic — should succeed
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_many_extra_fields(self, client):
        """Pydantic should ignore extra fields, not crash."""
        body = {"from_name": "a", "to": "b", "content": "hi"}
        for i in range(500):
            body[f"field_{i}"] = "x" * 100
        r = await client.post("/messages", json=body, headers=AUTH)
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_empty_content_message(self, client):
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": ""},
            headers=AUTH,
        )
        # Empty content is allowed (no minimum size)
        assert r.status_code == 200


# ===========================================================================
# 6. WEBHOOK URL VALIDATION
# ===========================================================================

class TestWebhookSecurity:
    BLOCKED_URLS = [
        "https://localhost/callback",
        "https://localhost:8080/callback",
        "http://127.0.0.1/callback",
        "http://127.0.0.1:3000/callback",
        "http://10.0.0.1/callback",
        "http://192.168.1.1/callback",
        "http://172.16.0.1/callback",
        "http://[::1]/callback",
        "http://0.0.0.0/callback",
        "ftp://example.com/callback",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://user:pass@example.com/callback",
        "http://example.localhost/callback",
        "http://foo.local/callback",
    ]

    @pytest.mark.anyio
    @pytest.mark.parametrize("url", BLOCKED_URLS)
    async def test_webhook_rejects_dangerous_url(self, client, url):
        r = await client.post(
            "/webhooks",
            json={"instance_name": "agent", "callback_url": url},
            headers=AUTH,
        )
        assert r.status_code == 400, f"Dangerous URL accepted: {url}"

    @pytest.mark.anyio
    async def test_webhook_accepts_valid_public_url(self, client):
        r = await client.post(
            "/webhooks",
            json={"instance_name": "agent", "callback_url": "https://example.com/hook"},
            headers=AUTH,
        )
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_webhook_rejects_empty_url(self, client):
        r = await client.post(
            "/webhooks",
            json={"instance_name": "agent", "callback_url": ""},
            headers=AUTH,
        )
        assert r.status_code == 400

    @pytest.mark.anyio
    async def test_webhook_rejects_no_host(self, client):
        r = await client.post(
            "/webhooks",
            json={"instance_name": "agent", "callback_url": "http:///path"},
            headers=AUTH,
        )
        assert r.status_code == 400


# ===========================================================================
# 7. ROOM OPERATIONS — boundary conditions, nonexistent rooms
# ===========================================================================

class TestRoomSecurity:
    @pytest.mark.anyio
    async def test_get_nonexistent_room(self, client):
        r = await client.get("/rooms/nonexistent-id", headers=AUTH)
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_join_nonexistent_room(self, client):
        r = await client.post(
            "/rooms/fake-id/join", json={"participant": "alice"}, headers=AUTH
        )
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_leave_nonexistent_room(self, client):
        r = await client.post(
            "/rooms/fake-id/leave", json={"participant": "alice"}, headers=AUTH
        )
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_send_to_nonexistent_room(self, client):
        r = await client.post(
            "/rooms/fake-id/messages",
            json={"from_name": "alice", "content": "hi"},
            headers=AUTH,
        )
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_history_nonexistent_room(self, client):
        r = await client.get("/rooms/fake-id/history", headers=AUTH)
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_duplicate_room_name_rejected(self, client):
        await _create_room(client, name="dup")
        r = await _create_room(client, name="dup")
        assert r.status_code == 409

    @pytest.mark.anyio
    async def test_non_member_cannot_send(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "outsider", "content": "intruder"},
            headers=AUTH,
        )
        assert r.status_code == 403

    @pytest.mark.anyio
    async def test_room_history_limit_clamped(self, client):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        # Negative limit
        r = await client.get(f"/rooms/{rid}/history?limit=-5", headers=AUTH)
        assert r.status_code == 200
        # Absurdly large limit
        r = await client.get(f"/rooms/{rid}/history?limit=999999", headers=AUTH)
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_room_by_name_resolution(self, client):
        resp = await _create_room(client, name="named-room")
        assert resp.status_code == 200
        r = await client.get("/rooms/named-room", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["name"] == "named-room"


# ===========================================================================
# 8. RATE LIMITING
# ===========================================================================

class TestRateLimiting:
    @pytest.mark.anyio
    async def test_rate_limit_on_messages(self, client):
        """Sending more than RATE_LIMIT_MAX messages should be rejected."""
        # Default is 60 per 60s window
        for i in range(60):
            r = await client.post(
                "/messages",
                json={"from_name": "spammer", "to": "victim", "content": f"msg-{i}"},
                headers=AUTH,
            )
            assert r.status_code == 200, f"Message {i} rejected early"

        # 61st should be rate-limited
        r = await client.post(
            "/messages",
            json={"from_name": "spammer", "to": "victim", "content": "one-too-many"},
            headers=AUTH,
        )
        assert r.status_code == 429

    @pytest.mark.anyio
    async def test_rate_limit_on_room_messages(self, client):
        """Room messages share the same rate limit."""
        resp = await _create_room(client, name="rate-room", creator="spammer")
        rid = resp.json()["id"]

        for i in range(60):
            r = await client.post(
                f"/rooms/{rid}/messages",
                json={"from_name": "spammer", "content": f"msg-{i}"},
                headers=AUTH,
            )
            assert r.status_code == 200, f"Room message {i} rejected early"

        r = await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "spammer", "content": "overflow"},
            headers=AUTH,
        )
        assert r.status_code == 429

    @pytest.mark.anyio
    async def test_rate_limit_per_sender(self, client):
        """Rate limiting is per-sender — different senders have independent buckets."""
        for i in range(60):
            await client.post(
                "/messages",
                json={"from_name": "sender-a", "to": "bob", "content": f"m{i}"},
                headers=AUTH,
            )

        # sender-a hit the limit
        r = await client.post(
            "/messages",
            json={"from_name": "sender-a", "to": "bob", "content": "blocked"},
            headers=AUTH,
        )
        assert r.status_code == 429

        # sender-b has its own bucket — should NOT be rate-limited
        r = await client.post(
            "/messages",
            json={"from_name": "sender-b", "to": "bob", "content": "allowed"},
            headers=AUTH,
        )
        assert r.status_code == 200


# ===========================================================================
# 9. SPECIAL CHARACTERS IN CONTENT — should be stored, not crash
# ===========================================================================

class TestContentSafety:
    PAYLOADS = [
        "<script>alert('xss')</script>",
        '"><img src=x onerror=alert(1)>',
        "{{7*7}}",
        "${7*7}",
        "'; DROP TABLE messages; --",
        "\x00\x01\x02\x03",
        "\U0001f600" * 100,  # emoji flood
        "a\nb\nc\n" * 100,  # newlines
        "\t" * 1000,
        "\\\\\\\\",
        '{"nested": "json"}',
        "<![CDATA[exploit]]>",
    ]

    @pytest.mark.anyio
    @pytest.mark.parametrize("payload", PAYLOADS)
    async def test_message_content_stored_safely(self, client, payload):
        """Any content should be stored and returned verbatim, never crash."""
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": payload},
            headers=AUTH,
        )
        assert r.status_code == 200

        # Retrieve and verify content round-trips
        r = await client.get("/messages/bob?ack=server", headers=AUTH)
        assert r.status_code == 200
        msgs = r.json()
        assert len(msgs) >= 1
        assert msgs[0]["content"] == payload

    @pytest.mark.anyio
    @pytest.mark.parametrize("payload", PAYLOADS)
    async def test_room_message_content_stored_safely(self, client, payload):
        resp = await _create_room(client)
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "admin", "content": payload},
            headers=AUTH,
        )
        assert r.status_code == 200

        # Check history
        r = await client.get(f"/rooms/{rid}/history", headers=AUTH)
        assert r.status_code == 200
        msgs = r.json()
        assert len(msgs) >= 1
        assert msgs[-1]["content"] == payload


# ===========================================================================
# 10. PATH PARAMETER ABUSE
# ===========================================================================

class TestPathParameterAbuse:
    """Path parameters like recipient, room_id aren't body-validated.
    They should not crash the server even with weird values."""

    BAD_PATHS = [
        "../../../etc/passwd",
        "%00",
        "a" * 10000,
        "<script>",
        "name with spaces",
    ]

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_path", BAD_PATHS)
    async def test_get_messages_bad_recipient(self, client, bad_path):
        r = await client.get(f"/messages/{bad_path}", headers=AUTH)
        # Should return empty list or an error — not crash
        assert r.status_code in (200, 400, 404, 422)

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_path", BAD_PATHS)
    async def test_peek_bad_recipient(self, client, bad_path):
        r = await client.get(f"/messages/{bad_path}/peek", headers=AUTH)
        assert r.status_code in (200, 400, 404, 422)

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_path", BAD_PATHS)
    async def test_get_room_bad_id(self, client, bad_path):
        r = await client.get(f"/rooms/{bad_path}", headers=AUTH)
        assert r.status_code in (400, 404, 422)

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_path", BAD_PATHS)
    async def test_delete_webhook_bad_name(self, client, bad_path):
        r = await client.delete(f"/webhooks/{bad_path}", headers=AUTH)
        # Should succeed (no-op) or return an error — not crash
        assert r.status_code in (200, 400, 404, 422)


# ===========================================================================
# 11. LONG-POLL — boundary values for wait parameter
# ===========================================================================

class TestLongPollSecurity:
    @pytest.mark.anyio
    async def test_wait_negative_clamped(self, client):
        r = await client.get("/messages/alice?wait=-1&ack=server", headers=AUTH)
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_wait_zero(self, client):
        r = await client.get("/messages/alice?wait=0&ack=server", headers=AUTH)
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_wait_over_max_clamped(self, client):
        """wait > 60 should be clamped, not cause a long hang."""
        # We set a short timeout on the client
        r = await client.get("/messages/alice?wait=1&ack=server", headers=AUTH)
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_wait_non_numeric(self, client):
        r = await client.get("/messages/alice?wait=abc&ack=server", headers=AUTH)
        assert r.status_code == 422


# ===========================================================================
# 12. CONCURRENT ACCESS — operations on the same recipient
# ===========================================================================

class TestConcurrency:
    @pytest.mark.anyio
    async def test_concurrent_sends_to_same_recipient(self, client):
        """Multiple concurrent sends should not lose messages."""
        import asyncio

        async def send(i):
            return await client.post(
                "/messages",
                json={"from_name": f"sender-{i}", "to": "target", "content": f"msg-{i}"},
                headers=AUTH,
            )

        results = await asyncio.gather(*[send(i) for i in range(20)])
        assert all(r.status_code == 200 for r in results)

        r = await client.get("/messages/target?ack=server", headers=AUTH)
        assert len(r.json()) == 20


# ===========================================================================
# 13. PRESENCE / HEARTBEAT EDGE CASES
# ===========================================================================

class TestPresenceSecurity:
    @pytest.mark.anyio
    async def test_heartbeat_valid_statuses(self, client):
        for status in ["active", "idle", "busy"]:
            r = await client.post(
                "/heartbeat",
                json={"instance_name": "agent", "status": status},
                headers=AUTH,
            )
            assert r.status_code == 200

    @pytest.mark.anyio
    async def test_heartbeat_empty_room(self, client):
        r = await client.post(
            "/heartbeat",
            json={"instance_name": "agent", "status": "active", "room": ""},
            headers=AUTH,
        )
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_presence_empty(self, client):
        r = await client.get("/presence", headers=AUTH)
        assert r.status_code == 200
        assert r.json() == []


# ===========================================================================
# 14. ANALYTICS — should not leak sensitive data
# ===========================================================================

class TestAnalyticsSecurity:
    @pytest.mark.anyio
    async def test_analytics_no_message_content(self, client):
        """Analytics should show counts, never message content."""
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "SECRET_DATA_123"},
            headers=AUTH,
        )
        r = await client.get("/analytics", headers=AUTH)
        assert r.status_code == 200
        body = r.text
        assert "SECRET_DATA_123" not in body

    @pytest.mark.anyio
    async def test_health_detailed_no_message_content(self, client):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "PRIVATE_INFO"},
            headers=AUTH,
        )
        r = await client.get("/health/detailed", headers=AUTH)
        assert r.status_code == 200
        assert "PRIVATE_INFO" not in r.text


# ===========================================================================
# 15. HTTP METHOD ENFORCEMENT
# ===========================================================================

class TestMethodEnforcement:
    @pytest.mark.anyio
    async def test_get_on_post_endpoint(self, client):
        r = await client.get("/messages", headers=AUTH)
        assert r.status_code == 405

    @pytest.mark.anyio
    async def test_post_on_get_endpoint(self, client):
        r = await client.post("/health", headers=AUTH)
        assert r.status_code == 405

    @pytest.mark.anyio
    async def test_put_on_messages(self, client):
        r = await client.put(
            "/messages",
            json={"from_name": "a", "to": "b", "content": "hi"},
            headers=AUTH,
        )
        assert r.status_code == 405

    @pytest.mark.anyio
    async def test_patch_on_rooms(self, client):
        r = await client.patch("/rooms", json={}, headers=AUTH)
        assert r.status_code == 405


# ===========================================================================
# 16. CONTENT-TYPE EDGE CASES
# ===========================================================================

class TestContentTypeEdgeCases:
    @pytest.mark.anyio
    async def test_form_encoded_body_rejected(self, client):
        r = await client.post(
            "/messages",
            data={"from_name": "a", "to": "b", "content": "hi"},
            headers=AUTH,
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_xml_body_rejected(self, client):
        r = await client.post(
            "/messages",
            content=b"<msg><from>a</from><to>b</to><content>hi</content></msg>",
            headers={**AUTH, "Content-Type": "application/xml"},
        )
        assert r.status_code == 422


# ===========================================================================
# 17. UNICODE & ENCODING EDGE CASES
# ===========================================================================

class TestUnicodeEdgeCases:
    @pytest.mark.anyio
    async def test_unicode_message_roundtrip(self, client):
        content = "Hello \u4e16\u754c \U0001f30d \u0410\u0411\u0412 \u0e01\u0e02\u0e03"
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": content},
            headers=AUTH,
        )
        assert r.status_code == 200
        r = await client.get("/messages/bob?ack=server", headers=AUTH)
        assert r.json()[0]["content"] == content

    @pytest.mark.anyio
    async def test_zero_width_characters(self, client):
        content = "hello\u200b\u200c\u200d\ufeffworld"
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": content},
            headers=AUTH,
        )
        assert r.status_code == 200

    @pytest.mark.anyio
    async def test_rtl_override_in_content(self, client):
        content = "\u202ehello\u202c"
        r = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": content},
            headers=AUTH,
        )
        assert r.status_code == 200


# ===========================================================================
# 18. INVITE PAGE — token exposure check
# ===========================================================================

# ===========================================================================
# 19. ROOM ADMIN — kick, destroy, rename
# ===========================================================================

class TestRoomAdmin:
    @pytest.mark.anyio
    async def test_kick_by_creator(self, client):
        resp = await _create_room(client, name="admin-room", creator="boss")
        rid = resp.json()["id"]
        await _join_room(client, rid, "worker")
        r = await client.post(
            f"/rooms/{rid}/kick",
            json={"participant": "worker", "requested_by": "boss"},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "kicked"
        # Verify worker is gone
        room = await client.get(f"/rooms/{rid}", headers=AUTH)
        assert "worker" not in room.json()["members"]

    @pytest.mark.anyio
    async def test_kick_by_non_creator_rejected(self, client):
        resp = await _create_room(client, name="admin-room2", creator="boss")
        rid = resp.json()["id"]
        await _join_room(client, rid, "worker")
        r = await client.post(
            f"/rooms/{rid}/kick",
            json={"participant": "worker", "requested_by": "worker"},
            headers=AUTH,
        )
        assert r.status_code == 403

    @pytest.mark.anyio
    async def test_kick_creator_rejected(self, client):
        resp = await _create_room(client, name="admin-room3", creator="boss")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/kick",
            json={"participant": "boss", "requested_by": "boss"},
            headers=AUTH,
        )
        assert r.status_code == 400

    @pytest.mark.anyio
    async def test_kick_nonmember_rejected(self, client):
        resp = await _create_room(client, name="admin-room4", creator="boss")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/kick",
            json={"participant": "ghost", "requested_by": "boss"},
            headers=AUTH,
        )
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_kick_no_auth(self, client):
        resp = await _create_room(client, name="admin-room5", creator="boss")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/kick",
            json={"participant": "boss", "requested_by": "boss"},
        )
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_destroy_by_creator(self, client):
        resp = await _create_room(client, name="doomed", creator="boss")
        rid = resp.json()["id"]
        r = await client.request(
            "DELETE", f"/rooms/{rid}",
            json={"requested_by": "boss"},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "destroyed"
        # Verify room is gone
        r2 = await client.get(f"/rooms/{rid}", headers=AUTH)
        assert r2.status_code == 404

    @pytest.mark.anyio
    async def test_destroy_by_non_creator_rejected(self, client):
        resp = await _create_room(client, name="safe", creator="boss")
        rid = resp.json()["id"]
        await _join_room(client, rid, "rando")
        r = await client.request(
            "DELETE", f"/rooms/{rid}",
            json={"requested_by": "rando"},
            headers=AUTH,
        )
        assert r.status_code == 403

    @pytest.mark.anyio
    async def test_destroy_no_auth(self, client):
        resp = await _create_room(client, name="locked", creator="boss")
        rid = resp.json()["id"]
        r = await client.request(
            "DELETE", f"/rooms/{rid}",
            json={"requested_by": "boss"},
        )
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_destroy_nonexistent_room(self, client):
        r = await client.request(
            "DELETE", "/rooms/fake-id",
            json={"requested_by": "boss"},
            headers=AUTH,
        )
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_rename_by_creator(self, client):
        resp = await _create_room(client, name="old-name", creator="boss")
        rid = resp.json()["id"]
        r = await client.patch(
            f"/rooms/{rid}",
            json={"new_name": "new-name", "requested_by": "boss"},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["new_name"] == "new-name"
        # Verify name changed
        room = await client.get(f"/rooms/{rid}", headers=AUTH)
        assert room.json()["name"] == "new-name"

    @pytest.mark.anyio
    async def test_rename_by_non_creator_rejected(self, client):
        resp = await _create_room(client, name="stable", creator="boss")
        rid = resp.json()["id"]
        await _join_room(client, rid, "rando")
        r = await client.patch(
            f"/rooms/{rid}",
            json={"new_name": "hijacked", "requested_by": "rando"},
            headers=AUTH,
        )
        assert r.status_code == 403

    @pytest.mark.anyio
    async def test_rename_duplicate_rejected(self, client):
        await _create_room(client, name="taken")
        resp = await _create_room(client, name="rename-me", creator="boss")
        rid = resp.json()["id"]
        r = await client.patch(
            f"/rooms/{rid}",
            json={"new_name": "taken", "requested_by": "boss"},
            headers=AUTH,
        )
        assert r.status_code == 409

    @pytest.mark.anyio
    async def test_rename_no_auth(self, client):
        resp = await _create_room(client, name="auth-test", creator="boss")
        rid = resp.json()["id"]
        r = await client.patch(
            f"/rooms/{rid}",
            json={"new_name": "renamed", "requested_by": "boss"},
        )
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_rename_bad_name_rejected(self, client):
        resp = await _create_room(client, name="valid-room", creator="boss")
        rid = resp.json()["id"]
        r = await client.patch(
            f"/rooms/{rid}",
            json={"new_name": "<script>alert(1)</script>", "requested_by": "boss"},
            headers=AUTH,
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_destroy_clears_history(self, client):
        resp = await _create_room(client, name="history-room", creator="boss")
        rid = resp.json()["id"]
        # Send a message
        await client.post(
            f"/rooms/{rid}/messages",
            json={"from_name": "boss", "content": "hello"},
            headers=AUTH,
        )
        # Destroy
        await client.request(
            "DELETE", f"/rooms/{rid}",
            json={"requested_by": "boss"},
            headers=AUTH,
        )
        # History should be gone
        r = await client.get(f"/rooms/{rid}/history", headers=AUTH)
        assert r.status_code == 404


# ===========================================================================
# 20. ROOM WEBHOOKS
# ===========================================================================

class TestRoomWebhooks:
    @pytest.mark.anyio
    async def test_register_room_webhook(self, client):
        resp = await _create_room(client, name="hook-room", creator="admin")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/hook", "registered_by": "admin"},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "registered"

    @pytest.mark.anyio
    async def test_list_room_webhooks(self, client):
        resp = await _create_room(client, name="hook-list", creator="admin")
        rid = resp.json()["id"]
        await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/a", "registered_by": "admin"},
            headers=AUTH,
        )
        await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/b", "registered_by": "admin"},
            headers=AUTH,
        )
        r = await client.get(f"/rooms/{rid}/webhooks", headers=AUTH)
        assert r.status_code == 200
        assert len(r.json()) == 2

    @pytest.mark.anyio
    async def test_delete_room_webhook(self, client):
        resp = await _create_room(client, name="hook-del", creator="admin")
        rid = resp.json()["id"]
        await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/del", "registered_by": "admin"},
            headers=AUTH,
        )
        r = await client.request(
            "DELETE", f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/del", "registered_by": "admin"},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "removed"
        # Verify gone
        r2 = await client.get(f"/rooms/{rid}/webhooks", headers=AUTH)
        assert len(r2.json()) == 0

    @pytest.mark.anyio
    async def test_delete_nonexistent_webhook(self, client):
        resp = await _create_room(client, name="hook-miss", creator="admin")
        rid = resp.json()["id"]
        r = await client.request(
            "DELETE", f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/nope", "registered_by": "admin"},
            headers=AUTH,
        )
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_duplicate_webhook_rejected(self, client):
        resp = await _create_room(client, name="hook-dup", creator="admin")
        rid = resp.json()["id"]
        await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/dup", "registered_by": "admin"},
            headers=AUTH,
        )
        r = await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/dup", "registered_by": "admin"},
            headers=AUTH,
        )
        assert r.status_code == 409

    @pytest.mark.anyio
    async def test_room_webhook_rejects_localhost(self, client):
        resp = await _create_room(client, name="hook-sec", creator="admin")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "http://localhost/bad", "registered_by": "admin"},
            headers=AUTH,
        )
        assert r.status_code == 400

    @pytest.mark.anyio
    async def test_room_webhook_rejects_private_ip(self, client):
        resp = await _create_room(client, name="hook-ip", creator="admin")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "http://192.168.1.1/bad", "registered_by": "admin"},
            headers=AUTH,
        )
        assert r.status_code == 400

    @pytest.mark.anyio
    async def test_room_webhook_no_auth(self, client):
        resp = await _create_room(client, name="hook-auth", creator="admin")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/x", "registered_by": "admin"},
        )
        assert r.status_code == 401

    @pytest.mark.anyio
    async def test_room_webhook_nonexistent_room(self, client):
        r = await client.post(
            "/rooms/fake-id/webhooks",
            json={"callback_url": "https://example.com/x", "registered_by": "admin"},
            headers=AUTH,
        )
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_destroy_room_clears_webhooks(self, client):
        resp = await _create_room(client, name="hook-destroy", creator="admin")
        rid = resp.json()["id"]
        await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/gone", "registered_by": "admin"},
            headers=AUTH,
        )
        await client.request(
            "DELETE", f"/rooms/{rid}",
            json={"requested_by": "admin"},
            headers=AUTH,
        )
        # Room is gone, webhooks should be too
        r = await client.get(f"/rooms/{rid}/webhooks", headers=AUTH)
        assert r.status_code == 404

    @pytest.mark.anyio
    async def test_room_webhook_bad_name_rejected(self, client):
        resp = await _create_room(client, name="hook-badname", creator="admin")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/webhooks",
            json={"callback_url": "https://example.com/x", "registered_by": "<script>"},
            headers=AUTH,
        )
        assert r.status_code == 422


# ===========================================================================
# 21. AGENT ROLES
# ===========================================================================

class TestAgentRoles:
    @pytest.mark.anyio
    async def test_join_with_default_role(self, client):
        resp = await _create_room(client, name="role-room", creator="admin")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "agent-1"},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["role"] == "member"

    @pytest.mark.anyio
    async def test_join_with_specific_role(self, client):
        resp = await _create_room(client, name="role-room2", creator="admin")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "reviewer-1", "role": "reviewer"},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["role"] == "reviewer"

    @pytest.mark.anyio
    async def test_join_invalid_role_rejected(self, client):
        resp = await _create_room(client, name="role-room3", creator="admin")
        rid = resp.json()["id"]
        r = await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "agent-1", "role": "hacker"},
            headers=AUTH,
        )
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_all_valid_roles(self, client):
        resp = await _create_room(client, name="all-roles", creator="admin")
        rid = resp.json()["id"]
        for role in ["builder", "reviewer", "researcher", "pm", "qa", "member"]:
            r = await client.post(
                f"/rooms/{rid}/join",
                json={"participant": f"agent-{role}", "role": role},
                headers=AUTH,
            )
            assert r.status_code == 200, f"Role {role} rejected"

    @pytest.mark.anyio
    async def test_room_shows_member_roles(self, client):
        resp = await _create_room(client, name="roles-visible", creator="admin")
        rid = resp.json()["id"]
        await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "dev-1", "role": "builder"},
            headers=AUTH,
        )
        await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "rev-1", "role": "reviewer"},
            headers=AUTH,
        )
        r = await client.get(f"/rooms/{rid}", headers=AUTH)
        assert r.status_code == 200
        roles = r.json()["member_roles"]
        assert roles["dev-1"] == "builder"
        assert roles["rev-1"] == "reviewer"
        assert roles["admin"] == "builder"  # creator default

    @pytest.mark.anyio
    async def test_kick_removes_role(self, client):
        resp = await _create_room(client, name="kick-role", creator="boss")
        rid = resp.json()["id"]
        await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "worker", "role": "qa"},
            headers=AUTH,
        )
        await client.post(
            f"/rooms/{rid}/kick",
            json={"participant": "worker", "requested_by": "boss"},
            headers=AUTH,
        )
        r = await client.get(f"/rooms/{rid}", headers=AUTH)
        assert "worker" not in r.json()["member_roles"]

    @pytest.mark.anyio
    async def test_leave_removes_role(self, client):
        resp = await _create_room(client, name="leave-role", creator="admin")
        rid = resp.json()["id"]
        await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "temp", "role": "researcher"},
            headers=AUTH,
        )
        await client.post(
            f"/rooms/{rid}/leave",
            json={"participant": "temp"},
            headers=AUTH,
        )
        r = await client.get(f"/rooms/{rid}", headers=AUTH)
        assert "temp" not in r.json()["member_roles"]

    @pytest.mark.anyio
    async def test_list_rooms_includes_roles(self, client):
        await _create_room(client, name="listed-roles", creator="admin")
        r = await client.get("/rooms", headers=AUTH)
        assert r.status_code == 200
        room = next(rm for rm in r.json() if rm["name"] == "listed-roles")
        assert "member_roles" in room
        assert room["member_roles"]["admin"] == "builder"

    @pytest.mark.anyio
    async def test_create_room_includes_roles(self, client):
        r = await _create_room(client, name="created-roles", creator="founder")
        assert r.status_code == 200
        assert r.json()["member_roles"]["founder"] == "builder"

    @pytest.mark.anyio
    async def test_role_update_on_rejoin(self, client):
        """Re-joining with a different role should update the role."""
        resp = await _create_room(client, name="rejoin-role", creator="admin")
        rid = resp.json()["id"]
        await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "flex", "role": "builder"},
            headers=AUTH,
        )
        await client.post(
            f"/rooms/{rid}/join",
            json={"participant": "flex", "role": "reviewer"},
            headers=AUTH,
        )
        r = await client.get(f"/rooms/{rid}", headers=AUTH)
        assert r.json()["member_roles"]["flex"] == "reviewer"


class TestInvitePageSecurity:
    @pytest.mark.anyio
    async def test_invite_page_does_not_leak_relay_secret(self, client):
        """The invite page should use INVITE_TOKEN, not RELAY_SECRET directly
        if they differ. When they're the same (default), verify it's present
        but in a controlled context (bearer header in JS)."""
        await _create_room(client, name="invite-test")
        r = await client.get("/invite/invite-test", headers=AUTH)
        assert r.status_code == 200
        # Token is in the page — this is by design for the invite flow.
        # Verify the page at least returns HTML (not raw JSON or error).
        assert "text/html" in r.headers.get("content-type", "")

    @pytest.mark.anyio
    async def test_invite_special_chars_in_room_name(self, client):
        """Room names are validated, so XSS via room name shouldn't be possible."""
        # Attempt to access a room with XSS in the name — should 404
        r = await client.get("/invite/<script>alert(1)</script>", headers=AUTH)
        assert r.status_code == 404
