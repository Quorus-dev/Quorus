"""Tests for quorus/auth/policy.py — the per-action authorization engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from quorus.auth.policy import (
    Decision,
    Mode,
    PolicyContext,
    evaluate,
    is_destructive,
    load_policy_for_tenant,
)


class TestEvaluate:
    def test_default_policy_allows_innocuous(self):
        """Empty policy + non-destructive action → ALLOW."""
        ctx = PolicyContext(actor="arav-claude", action="message.send", role="agent")
        r = evaluate(ctx)
        assert r.decision == Decision.ALLOW
        assert r.effective_decision == Decision.ALLOW
        assert r.mode == Mode.SHADOW

    def test_destructive_default_requires_human_for_agent(self):
        """Destructive action with no explicit rule + agent role → REQUIRE_HUMAN."""
        ctx = PolicyContext(actor="arav-claude", action="room.delete", role="agent")
        r = evaluate(ctx)
        assert r.decision == Decision.REQUIRE_HUMAN
        assert r.rule_id == "default.destructive"

    def test_destructive_does_not_trigger_for_human_role(self):
        """Humans aren't gated by the destructive-default safety net."""
        ctx = PolicyContext(actor="arav", action="room.delete", role="human")
        r = evaluate(ctx)
        assert r.decision == Decision.ALLOW

    def test_explicit_rule_overrides_default(self):
        policy = {
            "mode": "shadow",
            "rules": [{"action": "room.delete", "decision": "allow"}],
            "default_decision": "allow",
        }
        ctx = PolicyContext(actor="arav-claude", action="room.delete", role="agent")
        r = evaluate(ctx, policy)
        assert r.decision == Decision.ALLOW

    def test_when_role_gate(self):
        policy = {
            "mode": "hard",
            "rules": [
                {
                    "action": "invite.mint",
                    "decision": "require_human",
                    "when": {"role": "agent"},
                }
            ],
            "default_decision": "allow",
        }
        agent_ctx = PolicyContext(actor="x", action="invite.mint", role="agent")
        human_ctx = PolicyContext(actor="x", action="invite.mint", role="human")
        assert evaluate(agent_ctx, policy).decision == Decision.REQUIRE_HUMAN
        assert evaluate(human_ctx, policy).decision == Decision.ALLOW

    def test_shadow_mode_never_blocks(self):
        """Even DENY/REQUIRE_HUMAN decisions return ALLOW under SHADOW."""
        policy = {"mode": "shadow", "rules": [
            {"action": "x.y", "decision": "deny"}
        ], "default_decision": "allow"}
        ctx = PolicyContext(actor="a", action="x.y")
        r = evaluate(ctx, policy)
        assert r.decision == Decision.DENY
        assert r.effective_decision == Decision.ALLOW

    def test_soft_mode_logs_but_allows(self):
        policy = {"mode": "soft", "rules": [
            {"action": "x.y", "decision": "deny"}
        ], "default_decision": "allow"}
        ctx = PolicyContext(actor="a", action="x.y")
        r = evaluate(ctx, policy)
        assert r.effective_decision == Decision.ALLOW

    def test_hard_mode_blocks_deny(self):
        policy = {"mode": "hard", "rules": [
            {"action": "x.y", "decision": "deny"}
        ], "default_decision": "allow"}
        ctx = PolicyContext(actor="a", action="x.y")
        r = evaluate(ctx, policy)
        assert r.effective_decision == Decision.DENY

    def test_resource_match(self):
        policy = {
            "mode": "hard",
            "rules": [
                {
                    "action": "lock.acquire",
                    "resource": "/etc/passwd",
                    "decision": "deny",
                }
            ],
            "default_decision": "allow",
        }
        bad = PolicyContext(actor="a", action="lock.acquire", resource="/etc/passwd")
        ok = PolicyContext(actor="a", action="lock.acquire", resource="src/app.py")
        assert evaluate(bad, policy).decision == Decision.DENY
        assert evaluate(ok, policy).decision == Decision.ALLOW

    def test_invalid_decision_string_falls_back_to_allow(self):
        policy = {"mode": "shadow", "rules": [
            {"action": "x.y", "decision": "wat"}
        ]}
        ctx = PolicyContext(actor="a", action="x.y")
        r = evaluate(ctx, policy)
        assert r.decision == Decision.ALLOW


class TestLoadPolicyForTenant:
    def test_no_policy_dir_returns_default(self):
        p = load_policy_for_tenant("any", policy_dir=None)
        assert p["mode"] == "shadow"
        assert p["default_decision"] == "allow"

    def test_loads_tenant_specific(self, tmp_path: Path):
        (tmp_path / "acme.json").write_text(
            '{"mode":"hard","rules":[],"default_decision":"deny"}'
        )
        p = load_policy_for_tenant("acme", policy_dir=tmp_path)
        assert p["mode"] == "hard"
        assert p["default_decision"] == "deny"

    def test_falls_back_to_default_file(self, tmp_path: Path):
        (tmp_path / "_default.json").write_text(
            '{"mode":"soft","rules":[],"default_decision":"allow"}'
        )
        p = load_policy_for_tenant("missing-tenant", policy_dir=tmp_path)
        assert p["mode"] == "soft"

    def test_corrupt_file_falls_back_silently(self, tmp_path: Path):
        (tmp_path / "broken.json").write_text("not json {{{")
        p = load_policy_for_tenant("broken", policy_dir=tmp_path)
        # Silent fallback to built-in default.
        assert p["mode"] == "shadow"


class TestIsDestructive:
    @pytest.mark.parametrize(
        "action,expected",
        [
            ("room.delete", True),
            ("audit.purge", True),
            ("secret.read", True),
            ("message.send", False),
            ("lock.acquire", False),
            ("", False),
        ],
    )
    def test_destructive_classification(self, action, expected):
        assert is_destructive(action) is expected
