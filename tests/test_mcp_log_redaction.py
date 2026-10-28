"""The MCP server's log never carries the SSE token.

httpx logs each request URL at INFO, and the SSE connect sends the live token as
``?token=``, so it showed up in Claude's MCP log on every (re)connect (overseer,
2026-10-28). quorus-mcp keeps httpx and httpcore at WARNING.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from quorus_mcp import server  # noqa: F401  (importing it sets the logger levels)

TOKEN = "sse-tok-0123456789abcdef"


@pytest.mark.asyncio
async def test_an_sse_connect_never_logs_the_token(caplog):
    caplog.set_level(logging.DEBUG)  # even with LOG_LEVEL=DEBUG on the root
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=""))
    async with httpx.AsyncClient(transport=transport) as client:
        async with client.stream("GET", "http://127.0.0.1:8787/stream/alice",
                                 params={"token": TOKEN}) as resp:
            assert resp.status_code == 200
    assert TOKEN not in caplog.text


def test_httpx_and_httpcore_stay_quiet():
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING
