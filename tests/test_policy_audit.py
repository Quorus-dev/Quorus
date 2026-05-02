from __future__ import annotations

import uuid

import pytest

from quorus.auth.policy import Decision, Mode, PolicyContext, PolicyResult
from quorus.models.audit import AuditEvent, AuditLedger
from quorus.services.audit_svc import AuditService


@pytest.mark.asyncio
async def test_record_policy_evaluation_returns_signed_receipt(monkeypatch) -> None:
    service = AuditService()

    async def fake_record(**kwargs):
        event = AuditLedger(
            tenant_id=kwargs["tenant_id"],
            message_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
            event_type=kwargs["event_type"].value,
            actor=kwargs["actor"],
            target=kwargs["target"],
            details=kwargs["details"],
        )
        event.id = uuid.UUID("00000000-0000-0000-0000-000000000004")
        event.prev_hash = "0" * 64
        event.entry_hash = "1" * 64
        event.receipt_signature = "2" * 64
        return event

    monkeypatch.setattr(service, "record", fake_record)

    receipt = await service.record_policy_evaluation(
        tenant_id="tenant-1",
        ctx=PolicyContext(
            actor="arav-codex",
            action="room.delete",
            resource="ops-room",
            role="agent",
            extra={"api_key": "secret-value"},
        ),
        result=PolicyResult(
            decision=Decision.REQUIRE_HUMAN,
            rule_id="default.destructive",
            reason="requires approval",
            mode=Mode.HARD,
            effective_decision=Decision.REQUIRE_HUMAN,
        ),
    )

    assert receipt["entry_hash"] == "1" * 64
    assert receipt["prev_hash"] == "0" * 64
    assert receipt["receipt_signature"] == "2" * 64


@pytest.mark.asyncio
async def test_record_policy_evaluation_writes_audit_payload(monkeypatch) -> None:
    service = AuditService()
    captured = {}

    async def fake_record(**kwargs):
        captured.update(kwargs)
        event = AuditLedger(
            tenant_id=kwargs["tenant_id"],
            message_id=uuid.uuid4(),
            event_type=kwargs["event_type"].value,
        )
        event.id = uuid.uuid4()
        return event

    monkeypatch.setattr(service, "record", fake_record)

    await service.record_policy_evaluation(
        tenant_id="tenant-1",
        ctx=PolicyContext(
            actor="arav-codex",
            action="invite.mint",
            resource="launch-room",
            role="agent",
            extra={"nested": {"token": "mct_secret"}},
        ),
        result=PolicyResult(
            decision=Decision.DENY,
            rule_id="rule[0]",
            reason="blocked by tenant policy",
            mode=Mode.SHADOW,
            effective_decision=Decision.ALLOW,
        ),
    )

    assert captured["event_type"] == AuditEvent.POLICY_EVALUATED
    assert captured["actor"] == "arav-codex"
    assert captured["target"] == "invite.mint"
    assert captured["details"] == {
        "action": "invite.mint",
        "resource": "launch-room",
        "role": "agent",
        "rule_id": "rule[0]",
        "decision": "deny",
        "effective_decision": "allow",
        "reason": "blocked by tenant policy",
        "mode": "shadow",
        "extra": {"nested": {"token": "[redacted]"}},
    }
