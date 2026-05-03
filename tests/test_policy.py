"""Tests for quorus/auth/policy.py — the per-action authorization engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from quorus.auth.policy import (
    ALL_DM_ACTIONS,
    ALL_REFLEX_ACTIONS,
    ALL_SOCIAL_ACTIONS,
    Decision,
    Mode,
    PolicyContext,
    evaluate,
    evaluate_scoped,
    is_destructive,
    is_dm_action,
    is_reflex_action,
    is_social_action,
    load_policy_for_tenant,
    reset_rate_limits,
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


# ── Stream D — reflex / social / dm scoped checks ───────────────────────────


class TestActionEnumeration:
    """Sanity that the action vocabulary stays in lockstep with the verb-set."""

    def test_reflex_actions_present(self):
        assert "reflex:bid" in ALL_REFLEX_ACTIONS
        assert "reflex:claim" in ALL_REFLEX_ACTIONS

    def test_social_actions_cover_all_seven_verbs(self):
        for verb in ("claim", "release", "disagree", "defer",
                     "queue", "vote", "interrupt"):
            assert f"social:{verb}" in ALL_SOCIAL_ACTIONS

    def test_dm_actions(self):
        assert "dm:agent" in ALL_DM_ACTIONS

    def test_classifiers(self):
        assert is_reflex_action("reflex:bid")
        assert not is_reflex_action("social:claim")
        assert is_social_action("social:disagree")
        assert not is_social_action("reflex:claim")
        assert is_dm_action("dm:agent")
        assert not is_dm_action("social:claim")


class TestReflexScopedChecks:
    """``reflex:bid`` / ``reflex:claim`` are gated on room membership."""

    def setup_method(self):
        reset_rate_limits()

    def test_reflex_bid_allowed_for_room_member(self):
        ctx = PolicyContext(
            actor="arav-codex",
            action="reflex:bid",
            resource="proj",
            role="agent",
            extra={"room_members": {"arav-codex", "arav-claude"}},
        )
        r = evaluate_scoped(ctx)
        assert r.decision == Decision.ALLOW

    def test_reflex_bid_denied_for_non_member(self):
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        ctx = PolicyContext(
            actor="rogue-bot",
            action="reflex:bid",
            resource="proj",
            role="agent",
            extra={"room_members": {"arav-codex"}},
        )
        r = evaluate_scoped(ctx, policy)
        assert r.decision == Decision.DENY
        assert r.rule_id == "scoped.room_membership"
        assert r.effective_decision == Decision.DENY

    def test_reflex_claim_denied_when_membership_missing_for_agent(self):
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        ctx = PolicyContext(
            actor="arav-codex",
            action="reflex:claim",
            resource="proj",
            role="agent",
            extra=None,  # no membership info supplied
        )
        r = evaluate_scoped(ctx, policy)
        assert r.decision == Decision.DENY
        assert r.rule_id == "scoped.room_membership"


class TestSocialScopedChecks:
    """``social:<verb>`` actions: membership for all, rate-limit on blocking."""

    def setup_method(self):
        reset_rate_limits()

    def test_social_claim_allowed_for_member(self):
        ctx = PolicyContext(
            actor="arav-codex",
            action="social:claim",
            resource="proj",
            role="agent",
            extra={"room_members": ["arav-codex"]},
        )
        assert evaluate_scoped(ctx).decision == Decision.ALLOW

    def test_social_vote_denied_for_non_member(self):
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        ctx = PolicyContext(
            actor="rogue-bot",
            action="social:vote",
            resource="proj",
            role="agent",
            extra={"room_members": ["arav-codex"]},
        )
        r = evaluate_scoped(ctx, policy)
        assert r.decision == Decision.DENY

    def test_disagree_blocking_first_call_allowed(self):
        ctx = PolicyContext(
            actor="arav-claude",
            action="social:disagree",
            resource="proj",
            role="agent",
            extra={"room_members": {"arav-claude"}, "mode": "blocking"},
        )
        assert evaluate_scoped(ctx).decision == Decision.ALLOW

    def test_disagree_blocking_second_call_within_window_rate_limited(self):
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        members = {"arav-claude"}
        first = PolicyContext(
            actor="arav-claude",
            action="social:disagree",
            resource="proj",
            role="agent",
            extra={"room_members": members, "mode": "blocking"},
        )
        assert evaluate_scoped(first, policy).decision == Decision.ALLOW
        second = PolicyContext(
            actor="arav-claude",
            action="social:disagree",
            resource="proj",
            role="agent",
            extra={"room_members": members, "mode": "blocking"},
        )
        r = evaluate_scoped(second, policy)
        assert r.decision == Decision.DENY
        assert r.rule_id == "scoped.disagree_blocking_rate"
        assert "rate-limited" in r.reason

    def test_disagree_advisory_not_rate_limited(self):
        members = {"arav-claude"}
        for _ in range(5):
            ctx = PolicyContext(
                actor="arav-claude",
                action="social:disagree",
                resource="proj",
                role="agent",
                extra={"room_members": members, "mode": "advisory"},
            )
            assert evaluate_scoped(ctx).decision == Decision.ALLOW


class TestDMScopedChecks:
    """``dm:agent`` requires sender + recipient share a tenant."""

    def setup_method(self):
        reset_rate_limits()

    def test_dm_same_tenant_allowed(self):
        ctx = PolicyContext(
            actor="arav-codex",
            action="dm:agent",
            resource="arav-claude",
            role="agent",
            tenant_id="t1",
            extra={"recipient_tenant_id": "t1"},
        )
        assert evaluate_scoped(ctx).decision == Decision.ALLOW

    def test_dm_cross_tenant_denied(self):
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        ctx = PolicyContext(
            actor="arav-codex",
            action="dm:agent",
            resource="other-tenant-bot",
            role="agent",
            tenant_id="t1",
            extra={"recipient_tenant_id": "t2"},
        )
        r = evaluate_scoped(ctx, policy)
        assert r.decision == Decision.DENY
        assert r.rule_id == "scoped.dm_tenant"
        assert "cross-tenant" in r.reason

    def test_dm_missing_tenant_denied(self):
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        ctx = PolicyContext(
            actor="arav-codex",
            action="dm:agent",
            resource="x",
            role="agent",
            tenant_id=None,
            extra={"recipient_tenant_id": "t1"},
        )
        assert evaluate_scoped(ctx, policy).decision == Decision.DENY


class TestSocialRouteCedarGate:
    """CRIT-3: ``social:<verb>`` Cedar gate enforces room membership in HARD."""

    def setup_method(self):
        reset_rate_limits()

    def test_work_queue_blocked_by_cedar_for_non_member(self):
        """CRIT-3: the work-queue route maps ops to ``social:claim`` etc.

        A non-member actor with HARD policy mode must be denied. The
        route's runtime piggy-backs on the social membership gate so
        a single policy controls both surfaces.
        """
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        ctx = PolicyContext(
            actor="rogue-bot",
            action="social:claim",  # work_queue.op="claim" maps to this
            resource="proj",
            role="agent",
            extra={"room_members": ["alice", "bob"]},
        )
        r = evaluate_scoped(ctx, policy)
        assert r.decision == Decision.DENY
        assert r.rule_id == "scoped.room_membership"

    def test_dm_unknown_recipient_denied_in_hard_mode(self):
        """A DM to a recipient absent from the sender's tenant denies."""
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        ctx = PolicyContext(
            actor="agent-a",
            action="dm:agent",
            resource="agent-b-in-other-tenant",
            role="agent",
            tenant_id="tenant-a",
            extra={"recipient_tenant_id": "tenant-b"},
        )
        r = evaluate_scoped(ctx, policy)
        assert r.decision == Decision.DENY
        assert r.rule_id == "scoped.dm_tenant"


class TestEvaluateScopedFallthrough:
    """Unrelated actions pass through ``evaluate_scoped`` unchanged."""

    def setup_method(self):
        reset_rate_limits()

    def test_message_send_unaffected(self):
        ctx = PolicyContext(actor="arav", action="message.send", role="agent")
        assert evaluate_scoped(ctx).decision == Decision.ALLOW

    def test_destructive_default_still_applies(self):
        ctx = PolicyContext(actor="arav", action="room.delete", role="agent")
        # Routes through evaluate() first, hits destructive default.
        assert evaluate_scoped(ctx).decision == Decision.REQUIRE_HUMAN


class TestRateLimitLruCap:
    """L24 — ``_RATE_LIMIT_LAST`` must not grow without bound.

    The previous implementation was a plain ``dict`` that accumulated one
    entry per unique ``(actor, action)`` pair forever. With agent fleets
    using rotating ephemeral identities this could grow into millions of
    entries before a relay restart. We cap it at ``_RATE_LIMIT_MAX``
    using FIFO eviction.
    """

    def setup_method(self):
        reset_rate_limits()

    def test_rate_limit_last_lru_evicts_at_10k(self):
        from quorus.auth import policy as _policy
        # Insert _RATE_LIMIT_MAX + 50 distinct actors each issuing one
        # blocking disagree. After the cap is exceeded the oldest
        # entries should be evicted FIFO and the table size should
        # never exceed the cap.
        policy = {"mode": "hard", "rules": [], "default_decision": "allow"}
        cap = _policy._RATE_LIMIT_MAX
        n_extra = 50
        members = {"actor-0"}  # placeholder; rebuilt per actor below
        for i in range(cap + n_extra):
            actor = f"lru-actor-{i}"
            members = {actor}
            ctx = PolicyContext(
                actor=actor,
                action="social:disagree",
                resource="lru-room",
                role="agent",
                extra={"room_members": members, "mode": "blocking"},
            )
            r = evaluate_scoped(ctx, policy)
            assert r.decision == Decision.ALLOW

        # Cap holds — never exceeds the configured maximum.
        assert len(_policy._RATE_LIMIT_LAST) <= cap

        # FIFO eviction means the first 50 actors got evicted while the
        # most-recent ``cap`` survive. A re-rate-limit attempt for an
        # evicted actor should ALLOW (no record => fresh window), while
        # a re-attempt for a surviving actor should DENY (still cooling
        # down).
        evicted_actor = "lru-actor-0"
        evicted_ctx = PolicyContext(
            actor=evicted_actor,
            action="social:disagree",
            resource="lru-room",
            role="agent",
            extra={"room_members": {evicted_actor}, "mode": "blocking"},
        )
        r_evicted = evaluate_scoped(evicted_ctx, policy)
        assert r_evicted.decision == Decision.ALLOW

        survivor_actor = f"lru-actor-{cap + n_extra - 1}"
        survivor_ctx = PolicyContext(
            actor=survivor_actor,
            action="social:disagree",
            resource="lru-room",
            role="agent",
            extra={"room_members": {survivor_actor}, "mode": "blocking"},
        )
        r_survivor = evaluate_scoped(survivor_ctx, policy)
        assert r_survivor.decision == Decision.DENY
        assert "rate-limited" in r_survivor.reason
