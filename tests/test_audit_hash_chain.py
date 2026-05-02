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
    event = AuditLedger(
        tenant_id="tenant-1",
        message_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        event_type=AuditEvent.MCP_TOOL_CALLED.value,
        actor="arav-codex",
        target="send_room_message",
        details={"content": content},
        created_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )
    event.id = uuid.uuid4()
    return event


class _FakeAuditResult:
    def __init__(self, events: list[AuditLedger]):
        self.events = events

    def scalars(self):
        return SimpleNamespace(all=lambda: self.events)


class _FakeAuditSession:
    def __init__(self, events: list[AuditLedger]):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query):
        return _FakeAuditResult(self.events)


def _sealed_events() -> list[AuditLedger]:
    service = AuditService()
    first = _event("first")
    first_hash = service._seal_entry(first, None)
    second = _event("second")
    service._seal_entry(second, first_hash)
    return [first, second]


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


@pytest.mark.asyncio
async def test_verify_chain_accepts_valid_hash_chain(monkeypatch) -> None:
    events = _sealed_events()
    monkeypatch.setattr(
        "quorus.services.audit_svc.get_db_session",
        lambda: _FakeAuditSession(events),
    )

    result = await AuditService().verify_chain("tenant-1")

    assert result["verified"] is True
    assert result["checked_entries"] == 2
    assert result["last_hash"] == events[-1].entry_hash


@pytest.mark.asyncio
async def test_verify_chain_detects_tampered_entry(monkeypatch) -> None:
    events = _sealed_events()
    events[1].details = {"content": "tampered"}
    monkeypatch.setattr(
        "quorus.services.audit_svc.get_db_session",
        lambda: _FakeAuditSession(events),
    )

    result = await AuditService().verify_chain("tenant-1")

    assert result["verified"] is False
    assert result["reason"] == "entry_hash mismatch"
    assert result["entry_id"] == str(events[1].id)


@pytest.mark.asyncio
async def test_verify_chain_detects_broken_prev_hash(monkeypatch) -> None:
    events = _sealed_events()
    events[1].prev_hash = "0" * 64
    monkeypatch.setattr(
        "quorus.services.audit_svc.get_db_session",
        lambda: _FakeAuditSession(events),
    )

    result = await AuditService().verify_chain("tenant-1")

    assert result["verified"] is False
    assert result["reason"] == "prev_hash mismatch"
    assert result["entry_id"] == str(events[1].id)
