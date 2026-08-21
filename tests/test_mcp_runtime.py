"""Tests for the 2026-08 MCP server file split (``quorus_mcp.runtime``).

Covers:
* the back-compat re-export surface on ``quorus_mcp.server`` /
  ``quorus.mcp_server`` (moved helpers must remain reachable and identical),
* the new lock-held async reset (:func:`reset_runtime_state_locked`),
* the runtime loops resolving patchable helpers through the SERVER module
  namespace (the ``_srv()`` late-binding contract the split relies on).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from quorus_mcp import runtime

import quorus.mcp_server as mcp_server


def test_server_reexports_runtime_helpers():
    """Moved names must stay importable from the server module, unchanged."""
    for name in (
        "_clean_env",
        "_resolve_auth",
        "_validate_header_safe",
        "_validate_relay_url",
        "_fetch_relay_messages",
        "_ack_messages",
        "_heartbeat_loop",
        "_process_sse_event",
        "_get_sse_token",
        "_sse_listener",
        "_polling_fallback",
        "_mcp_lifespan",
    ):
        assert getattr(mcp_server, name) is getattr(runtime, name), name


def test_shim_and_package_module_are_same_object():
    """quorus.mcp_server must alias quorus_mcp.server (existing contract).

    Note: we deliberately do NOT assert ``quorus_mcp.mcp is mcp_server.mcp``
    — the package ``__init__`` binds ``mcp`` at first import, and other test
    files (``test_mcp_auth_fallback``) legitimately ``importlib.reload`` the
    server module, replacing ``server.mcp`` with a fresh instance.
    """
    import quorus_mcp.server as pkg_server

    assert mcp_server is pkg_server
    from quorus_mcp import mcp as pkg_mcp

    assert isinstance(pkg_mcp, type(mcp_server.mcp))


def test_sync_reset_is_documented_test_only():
    doc = mcp_server._reset_runtime_state.__doc__ or ""
    assert "TEST-ONLY" in doc


@pytest.mark.asyncio
async def test_reset_runtime_state_locked_clears_state():
    mcp_server._reset_runtime_state()
    sentinel = object()
    await mcp_server._set_active_session(sentinel)
    await mcp_server._append_pending_messages([{"id": "m1"}])

    await mcp_server.reset_runtime_state_locked()

    assert await mcp_server._get_active_session() is None
    assert await mcp_server._drain_pending_messages() == []
    assert mcp_server._http_client is None
    assert mcp_server._heartbeat_task is None
    assert mcp_server._active_sse_listener is None


@pytest.mark.asyncio
async def test_reset_runtime_state_locked_waits_for_session_lock():
    """The locked reset must serialize behind _active_session_lock."""
    mcp_server._reset_runtime_state()
    await mcp_server._active_session_lock.acquire()
    try:
        reset_task = asyncio.create_task(mcp_server.reset_runtime_state_locked())
        await asyncio.sleep(0.01)
        assert not reset_task.done()
    finally:
        mcp_server._active_session_lock.release()
    await asyncio.wait_for(reset_task, timeout=1)
    assert reset_task.done()


@pytest.mark.asyncio
async def test_runtime_loops_resolve_helpers_via_server_namespace():
    """Patching quorus.mcp_server helpers must be seen by runtime loops."""
    mcp_server._reset_runtime_state()

    class _Tripped:
        def breaker_state(self):
            return {"tripped": True, "failures": 3, "last_error": "boom"}

    fetched: list[int] = []
    stop_event = asyncio.Event()

    async def fake_fetch(wait):
        fetched.append(wait)
        stop_event.set()
        return ([], None, None)

    mcp_server._active_sse_listener = _Tripped()
    try:
        # Patch on the SERVER module; the loop lives in runtime.py.
        with patch.object(mcp_server, "_fetch_relay_messages", fake_fetch):
            await mcp_server._polling_fallback(stop_event)
    finally:
        mcp_server._active_sse_listener = None
        mcp_server._reset_runtime_state()

    assert fetched, "runtime._polling_fallback did not see the server-level patch"


def test_load_runtime_config_has_no_polling_keys():
    """The resolved settings must not carry the removed polling toggles."""
    settings = runtime.load_runtime_config()
    assert "enable_background_polling" not in settings
    assert set(settings) == {
        "config_file",
        "relay_url",
        "relay_secret",
        "api_key",
        "instance_name",
        "sse_enabled",
        "push_notification_method",
        "push_notification_channel",
    }
