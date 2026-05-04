"""Regression tests for /v1/audit route handlers.

Wave-5 Fix 7: ``/v1/audit/mcp-tool-call`` previously accepted arbitrary
``arguments`` dicts with no upper bound. A compromised agent could DoS
the Postgres ledger by sending multi-MB payloads. We now cap at 4 KB
BEFORE any DB write.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from quorus.auth.middleware import AuthContext
from quorus.routes.audit import McpToolAuditRequest, record_mcp_tool_call


class _FakeAuditService:
    async def record_mcp_tool_call(self, **kwargs):
        # If we ever reach this, the size cap let too much through.
        return kwargs


def _ctx() -> tuple[SimpleNamespace, AuthContext]:
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(audit_service=_FakeAuditService()))
    )
    auth = AuthContext(sub="agent-1", tenant_id="tenant-1")
    return request, auth


@pytest.mark.asyncio
async def test_oversized_arguments_rejected_413():
    """Wave-5 Fix 7 — payloads >4KB must 413 BEFORE the DB write."""
    request, auth = _ctx()
    # 8KB string under one key easily blows the 4KB cap.
    big_blob = "A" * 8000
    body = McpToolAuditRequest(
        tool_name="claim_task",
        arguments={"file_path": big_blob},
        mutating=True,
    )
    with pytest.raises(Exception) as exc_info:
        await record_mcp_tool_call(body, request, auth)
    # FastAPI's HTTPException — assert status_code 413 + cap message.
    assert getattr(exc_info.value, "status_code", None) == 413, exc_info.value
    detail = getattr(exc_info.value, "detail", "")
    assert "4KB" in str(detail) or "exceeds" in str(detail).lower(), detail


@pytest.mark.asyncio
async def test_under_cap_arguments_accepted():
    """Sanity — an under-cap payload still succeeds and reaches the
    audit service."""
    request, auth = _ctx()
    body = McpToolAuditRequest(
        tool_name="claim_task",
        arguments={"file_path": "small/path.py", "lines": 42},
        mutating=False,
    )
    result = await record_mcp_tool_call(body, request, auth)
    assert result["tool_name"] == "claim_task"
    assert result["actor"] == "agent-1"


@pytest.mark.asyncio
async def test_just_under_cap_accepted():
    """Boundary check — exactly at the 4KB cap should still be accepted
    so legitimate-but-large payloads aren't penalized."""
    request, auth = _ctx()
    # Compose a payload whose JSON size is right under 4096 bytes.
    blob = "x" * 3900
    body = McpToolAuditRequest(
        tool_name="claim_task",
        arguments={"data": blob},
        mutating=False,
    )
    result = await record_mcp_tool_call(body, request, auth)
    assert result["tool_name"] == "claim_task"
