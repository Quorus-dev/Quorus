"""Tests for thread-safe active session access."""

from __future__ import annotations

import asyncio

import pytest

import quorus.mcp_server as mcp_server


@pytest.mark.asyncio
async def test_concurrent_set_get_no_torn_reads():
    """N concurrent coroutines calling get/set must not raise or produce torn state."""
    mcp_server._reset_runtime_state()

    sentinel_a = object()
    sentinel_b = object()
    errors = []

    async def writer_a():
        for _ in range(500):
            await mcp_server._set_active_session(sentinel_a)
            await asyncio.sleep(0)

    async def writer_b():
        for _ in range(500):
            await mcp_server._set_active_session(sentinel_b)
            await asyncio.sleep(0)

    async def reader():
        for _ in range(500):
            val = await mcp_server._get_active_session()
            if val not in (None, sentinel_a, sentinel_b):
                errors.append(f"Unexpected value: {val!r}")
            await asyncio.sleep(0)

    await asyncio.gather(
        asyncio.create_task(writer_a()),
        asyncio.create_task(writer_b()),
        asyncio.create_task(reader()),
        asyncio.create_task(reader()),
    )

    assert errors == [], f"Torn reads detected: {errors}"
    mcp_server._reset_runtime_state()


@pytest.mark.asyncio
async def test_set_active_session_none_resets():
    """Setting session to None clears it cleanly."""
    mcp_server._reset_runtime_state()
    sentinel = object()
    await mcp_server._set_active_session(sentinel)
    assert await mcp_server._get_active_session() is sentinel

    await mcp_server._set_active_session(None)
    assert await mcp_server._get_active_session() is None
    mcp_server._reset_runtime_state()


@pytest.mark.asyncio
async def test_get_active_session_initially_none():
    """Fresh state must return None."""
    mcp_server._reset_runtime_state()
    assert await mcp_server._get_active_session() is None
