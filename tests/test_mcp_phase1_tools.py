"""Tests for the Phase 1 MCP tool wrappers — capability discovery,
tool catalog, persistent memory.

These tests follow the pattern in ``tests/test_mcp.py``: patch the module-
level ``RELAY_URL`` / ``INSTANCE_NAME`` constants, mock the httpx client,
and mock ``_audit_tool_call`` so the audit sidecar isn't exercised. The
goal is to prove the wrapper builds the right URL, body, and headers, and
handles the relevant status codes (404, 401-refresh, 5xx).

Coverage:

* publish_capability — happy path, audit-required-blocks-mutation
* lookup_capability — happy path, 404 returns friendly message
* search_capabilities — happy path with tokens, empty match
* register_tool — happy path, audit-required-blocks-mutation
* list_room_tools — happy path, empty list
* unregister_tool — happy path, 404 returns friendly message
* memory_set — happy path, audit-required-blocks-mutation
* memory_get — happy path, 404 returns friendly message
* memory_list — happy path, empty list
* memory_delete — happy path, 404 returns friendly message
"""
from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from quorus_mcp import phase1_tools

import quorus.mcp_server as mcp_server


@pytest.fixture(autouse=True)
def configure_mcp():
    """Patch module-level constants for every test."""
    patches = [
        patch.object(mcp_server, "RELAY_URL", "http://relay:8080"),
        patch.object(mcp_server, "RELAY_SECRET", "secret"),
        patch.object(mcp_server, "INSTANCE_NAME", "alice"),
        patch.object(mcp_server, "PUSH_NOTIFICATION_METHOD", None),
        patch.object(mcp_server, "PUSH_NOTIFICATION_CHANNEL", "mcp-tunnel"),
    ]
    if hasattr(mcp_server, "API_KEY"):
        patches.append(patch.object(mcp_server, "API_KEY", ""))
    # Audit sidecar is exercised in its own test module; here we mock it.
    patches.append(
        patch("quorus_mcp.tools._audit_tool_call", new_callable=AsyncMock),
    )
    # phase1_tools imports _audit_tool_call by name at module-load time, so
    # patch THAT binding too — otherwise our wrappers still call the real one.
    patches.append(
        patch("quorus_mcp.phase1_tools._audit_tool_call", new_callable=AsyncMock),
    )
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        mcp_server._reset_runtime_state()
        yield
        mcp_server._reset_runtime_state()


def _mock_response(
    *, status: int = 200, json_data: dict | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    if status >= 400:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "boom",
                request=MagicMock(),
                response=resp,
            )
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _mock_client(
    *, request_response: MagicMock,
) -> AsyncMock:
    """Build a mock httpx client whose ``request`` returns ``request_response``."""
    client = AsyncMock()
    client.is_closed = False
    client.request = AsyncMock(return_value=request_response)
    return client


# ---------------------------------------------------------------------------
# Capability discovery
# ---------------------------------------------------------------------------


class TestPublishCapability:
    async def test_happy_path_posts_manifest(self):
        resp = _mock_response(json_data={
            "ok": True,
            "manifest": {
                "participant": "alice",
                "capabilities": ["python", "pytest", "fastapi"],
            },
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.publish_capability(
                description="ships python services",
                supported_languages=["python"],
                capabilities=["python", "pytest", "fastapi"],
            )
        client.request.assert_called_once()
        args, kwargs = client.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://relay:8080/v1/capabilities/alice"
        assert kwargs["json"]["description"] == "ships python services"
        assert kwargs["json"]["capabilities"] == ["python", "pytest", "fastapi"]
        # Default supported_frameworks falls back to []
        assert kwargs["json"]["supported_frameworks"] == []
        assert "3 capabilities" in result

    async def test_audit_timeout_blocks_mutation(self):
        client = _mock_client(request_response=_mock_response())
        with (
            patch("quorus.mcp_server._get_http_client", return_value=client),
            patch(
                "quorus_mcp.phase1_tools._audit_tool_call",
                side_effect=httpx.ReadTimeout("audit timeout"),
            ),
        ):
            result = await phase1_tools.publish_capability(
                capabilities=["python"],
            )
        # Mutation must NOT have been attempted when required audit fails.
        client.request.assert_not_awaited()
        assert "timed out" in result.lower()


class TestLookupCapability:
    async def test_happy_path_returns_manifest_summary(self):
        resp = _mock_response(json_data={
            "manifest": {
                "participant": "bob",
                "description": "ships ruby services",
                "supported_languages": ["ruby"],
                "capabilities": ["ruby", "rails"],
            },
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.lookup_capability("bob")
        args, _ = client.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://relay:8080/v1/capabilities/bob"
        assert "bob" in result
        assert "ruby" in result

    async def test_404_returns_friendly_message(self):
        resp = _mock_response(status=404)
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.lookup_capability("ghost")
        assert "No manifest" in result
        # raise_for_status must not have been invoked on a 404 (handled inline)
        resp.raise_for_status.assert_not_called()


class TestSearchCapabilities:
    async def test_happy_path_with_tokens(self):
        resp = _mock_response(json_data={
            "query": ["python", "pytest"],
            "count": 1,
            "manifests": [
                {"participant": "alice", "capabilities": ["python", "pytest"]},
            ],
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.search_capabilities("python pytest")
        args, kwargs = client.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://relay:8080/v1/capabilities/search"
        assert kwargs["params"] == {"has": "python pytest"}
        assert "alice" in result
        assert "python, pytest" in result

    async def test_empty_match(self):
        resp = _mock_response(json_data={
            "query": ["haskell"],
            "count": 0,
            "manifests": [],
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.search_capabilities("haskell")
        assert "No matching" in result


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------


class TestRegisterTool:
    async def test_happy_path_posts_registration(self):
        resp = _mock_response(json_data={
            "ok": True,
            "tool": {
                "name": "github-mcp",
                "url": "https://github.example/mcp",
                "access": "room",
            },
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.register_tool(
                room_id="rid-1",
                name="github-mcp",
                url="https://github.example/mcp",
                access="room",
                description="GitHub MCP",
            )
        args, kwargs = client.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://relay:8080/v1/rooms/rid-1/tools"
        assert kwargs["json"]["name"] == "github-mcp"
        assert kwargs["json"]["access"] == "room"
        assert "github-mcp" in result

    async def test_audit_timeout_blocks_mutation(self):
        client = _mock_client(request_response=_mock_response())
        with (
            patch("quorus.mcp_server._get_http_client", return_value=client),
            patch(
                "quorus_mcp.phase1_tools._audit_tool_call",
                side_effect=httpx.ReadTimeout("audit timeout"),
            ),
        ):
            result = await phase1_tools.register_tool(
                "rid-1", "n", "https://x.example",
            )
        client.request.assert_not_awaited()
        assert "timed out" in result.lower()


class TestListRoomTools:
    async def test_happy_path(self):
        resp = _mock_response(json_data={
            "room_id": "rid-1",
            "count": 2,
            "tools": [
                {
                    "name": "github-mcp",
                    "url": "https://gh.example",
                    "access": "room",
                    "description": "GitHub",
                },
                {
                    "name": "stripe-mcp",
                    "url": "https://st.example",
                    "access": "public",
                    "description": "",
                },
            ],
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.list_room_tools("rid-1")
        args, _ = client.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://relay:8080/v1/rooms/rid-1/tools"
        assert "github-mcp" in result
        assert "stripe-mcp" in result

    async def test_empty_list(self):
        resp = _mock_response(json_data={
            "room_id": "rid-2", "count": 0, "tools": [],
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.list_room_tools("rid-2")
        assert "No tools" in result


class TestUnregisterTool:
    async def test_happy_path(self):
        resp = _mock_response(json_data={"ok": True, "removed": "github-mcp"})
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.unregister_tool("rid-1", "github-mcp")
        args, _ = client.request.call_args
        assert args[0] == "DELETE"
        assert args[1] == "http://relay:8080/v1/rooms/rid-1/tools/github-mcp"
        assert "removed" in result
        assert "github-mcp" in result

    async def test_404_returns_friendly_message(self):
        resp = _mock_response(status=404)
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.unregister_tool("rid-1", "ghost")
        assert "No tool" in result
        resp.raise_for_status.assert_not_called()


# ---------------------------------------------------------------------------
# Persistent memory
# ---------------------------------------------------------------------------


class TestMemorySet:
    async def test_happy_path_puts_value(self):
        resp = _mock_response(json_data={
            "ok": True,
            "entry": {
                "key": "scratch",
                "value": "hello",
                "visibility": "private",
                "size_bytes": 5,
            },
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.memory_set(
                "rid-1", "scratch", "hello",
            )
        args, kwargs = client.request.call_args
        assert args[0] == "PUT"
        assert args[1] == "http://relay:8080/v1/memory/alice/rid-1/scratch"
        assert kwargs["json"]["value"] == "hello"
        assert kwargs["json"]["visibility"] == "private"
        assert "5 bytes" in result
        assert "scratch" in result

    async def test_audit_timeout_blocks_mutation(self):
        client = _mock_client(request_response=_mock_response())
        with (
            patch("quorus.mcp_server._get_http_client", return_value=client),
            patch(
                "quorus_mcp.phase1_tools._audit_tool_call",
                side_effect=httpx.ReadTimeout("audit timeout"),
            ),
        ):
            result = await phase1_tools.memory_set("rid", "k", "v")
        client.request.assert_not_awaited()
        assert "timed out" in result.lower()


class TestMemoryGet:
    async def test_happy_path_self_owner(self):
        resp = _mock_response(json_data={
            "entry": {
                "key": "scratch",
                "value": "hello",
                "visibility": "private",
                "owner": "alice",
            },
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.memory_get("rid-1", "scratch")
        args, _ = client.request.call_args
        # No `owner` arg → defaults to INSTANCE_NAME ("alice").
        assert args[1] == "http://relay:8080/v1/memory/alice/rid-1/scratch"
        assert "hello" in result

    async def test_happy_path_explicit_owner(self):
        resp = _mock_response(json_data={
            "entry": {
                "key": "shared",
                "value": "data",
                "visibility": "room",
                "owner": "bob",
            },
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.memory_get(
                "rid-1", "shared", owner="bob",
            )
        args, _ = client.request.call_args
        assert args[1] == "http://relay:8080/v1/memory/bob/rid-1/shared"
        assert "data" in result

    async def test_404_returns_friendly_message(self):
        resp = _mock_response(status=404)
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.memory_get("rid-1", "ghost")
        assert "No entry" in result
        resp.raise_for_status.assert_not_called()


class TestMemoryList:
    async def test_happy_path(self):
        resp = _mock_response(json_data={
            "participant": "alice",
            "room_id": "rid-1",
            "count": 2,
            "entries": [
                {"key": "a", "visibility": "private", "size_bytes": 3},
                {"key": "b", "visibility": "room", "size_bytes": 9},
            ],
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.memory_list("rid-1")
        args, _ = client.request.call_args
        assert args[1] == "http://relay:8080/v1/memory/alice/rid-1"
        assert "a" in result and "b" in result

    async def test_empty_list(self):
        resp = _mock_response(json_data={
            "participant": "alice",
            "room_id": "rid-2",
            "count": 0,
            "entries": [],
        })
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.memory_list("rid-2")
        assert "No memory entries" in result


class TestMemoryDelete:
    async def test_happy_path(self):
        resp = _mock_response(json_data={"ok": True, "deleted": "scratch"})
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.memory_delete("rid-1", "scratch")
        args, _ = client.request.call_args
        assert args[0] == "DELETE"
        # Always uses INSTANCE_NAME (alice) as the participant.
        assert args[1] == "http://relay:8080/v1/memory/alice/rid-1/scratch"
        assert "deleted" in result

    async def test_404_returns_friendly_message(self):
        resp = _mock_response(status=404)
        client = _mock_client(request_response=resp)
        with patch("quorus.mcp_server._get_http_client", return_value=client):
            result = await phase1_tools.memory_delete("rid-1", "ghost")
        assert "No entry" in result
        resp.raise_for_status.assert_not_called()


# ---------------------------------------------------------------------------
# JWT-refresh-on-401 covered once for the shared helper
# ---------------------------------------------------------------------------


class TestJwtRefreshOn401:
    async def test_lookup_capability_retries_after_refresh(self):
        unauthorized = _mock_response(status=401)
        ok = _mock_response(json_data={
            "manifest": {"participant": "bob", "capabilities": []},
        })
        client = AsyncMock()
        client.is_closed = False
        # First call returns 401, second returns 200 after refresh.
        client.request = AsyncMock(side_effect=[unauthorized, ok])
        with (
            patch("quorus.mcp_server._get_http_client", return_value=client),
            patch(
                "quorus.mcp_server._refresh_jwt_on_401",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await phase1_tools.lookup_capability("bob")
        assert client.request.await_count == 2
        assert "bob" in result
