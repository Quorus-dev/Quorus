from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from quorus.auth.middleware import AuthContext
from quorus.models.audit import AuditEvent, AuditLedger
from quorus.routes.audit import McpToolAuditRequest, record_mcp_tool_call
from quorus.services.audit_svc import AuditService, _entry_hash, _redact


def _event(content: str = "hello") -> AuditLedger:
    return AuditLedger(
        tenant_id="tenant-1",
        message_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        event_type=AuditEvent.MCP_TOOL_CALLED.value,
        actor="arav-codex",
        target="send_room_message",
        details={"content": content},
        created_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )


def test_audit_entry_hash_is_deterministic_and_chained() -> None:
    first = _event()
    second = _event()

    first_hash = _entry_hash(first, None)
    assert _entry_hash(_event(), None) == first_hash
    assert _entry_hash(second, first_hash) != first_hash
    assert _entry_hash(_event("changed"), None) != first_hash


def test_audit_redacts_secret_arguments() -> None:
    redacted = _redact(
        {
            "api_key": "mct_secret",
            "nested": {"Authorization": "Bearer token", "room": "dev"},
        }
    )

    assert redacted["api_key"] == "[redacted]"
    assert redacted["nested"]["Authorization"] == "[redacted]"
    assert redacted["nested"]["room"] == "dev"


@pytest.mark.asyncio
async def test_record_mcp_tool_call_returns_signed_receipt(monkeypatch) -> None:
    service = AuditService()

    async def fake_record(**kwargs):
        event = _event()
        event.id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        event.prev_hash = "0" * 64
        event.entry_hash = "1" * 64
        event.receipt_signature = "2" * 64
        assert kwargs["event_type"] == AuditEvent.MCP_TOOL_CALLED
        assert kwargs["details"]["arguments"]["api_key"] == "[redacted]"
        return event

    monkeypatch.setattr(service, "record", fake_record)

    receipt = await service.record_mcp_tool_call(
        tenant_id="tenant-1",
        actor="arav-codex",
        tool_name="send_message",
        arguments={"api_key": "mct_secret"},
        mutating=True,
    )

    assert receipt["entry_hash"] == "1" * 64
    assert receipt["prev_hash"] == "0" * 64
    assert receipt["receipt_signature"] == "2" * 64


@pytest.mark.asyncio
async def test_record_mcp_tool_call_route_uses_auth_identity() -> None:
    class FakeAuditService:
        async def record_mcp_tool_call(self, **kwargs):
            return kwargs

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(audit_service=FakeAuditService()))
    )
    auth = AuthContext(sub="arav-codex", tenant_id="tenant-1")

    result = await record_mcp_tool_call(
        McpToolAuditRequest(
            tool_name="claim_task",
            arguments={"file_path": "packages/mcp/quorus_mcp/tools.py"},
            mutating=True,
        ),
        request,
        auth,
    )

    assert result["tenant_id"] == "tenant-1"
    assert result["actor"] == "arav-codex"
    assert result["tool_name"] == "claim_task"
    assert result["mutating"] is True
