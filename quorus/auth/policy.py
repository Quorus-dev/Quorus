"""Per-tenant authorization policy engine for AI agent actions.

Goal: every state-changing action an AI agent attempts (create room,
delete room, claim lock, send message, mint invite) is evaluated against
a tenant-scoped policy that returns ALLOW / DENY / REQUIRE_HUMAN. This is
the seatbelt that catches the kind of incident where Replit's AI deleted
a prod DB or Cursor's Opus wiped PocketOS — caught before mutation, not
after.

Three modes per tenant (configurable):

  SHADOW   — log every decision, NEVER block. Default for new tenants.
             Lets us collect data without breaking flows.
  SOFT     — log + post warning to room when DENY/REQUIRE_HUMAN, but
             still execute. Good middle stage.
  HARD     — DENY blocks the request (HTTP 403). REQUIRE_HUMAN parks the
             action waiting for an `approve` or `reject` over MCP.

The engine is intentionally simpler than Cedar / OPA / Biscuit — JSON
policy rules, easy to read, no new deps. We can graduate to Cedar later
once the data tells us what real rules look like.

Example policy (tenant scoped):

    {
      "mode": "shadow",
      "rules": [
        {"action": "room.delete",   "decision": "require_human"},
        {"action": "lock.acquire",  "decision": "allow"},
        {"action": "message.send",  "decision": "allow"},
        {"action": "invite.mint",   "decision": "require_human",
         "when": {"role": "agent"}}
      ],
      "default_decision": "allow"
    }

Real-world load-bearing constraint: this module CANNOT introduce new
dependencies (we're shipping in 2 days). Pure stdlib. Cedar / OPA come
in a follow-up sprint when we have a few weeks of real-policy data.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# ── Public types ────────────────────────────────────────────────────────────


class Decision(str, Enum):
    """Outcome of policy evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HUMAN = "require_human"


class Mode(str, Enum):
    """Tenant-level enforcement mode for the policy engine."""

    SHADOW = "shadow"   # log only, never block
    SOFT = "soft"       # log + warn, still execute
    HARD = "hard"       # block on DENY, park on REQUIRE_HUMAN


@dataclass(frozen=True)
class PolicyContext:
    """Inputs to policy evaluation. All fields except `actor` and `action` optional."""

    actor: str                   # agent name or human user (e.g. "arav-codex")
    action: str                  # canonical action verb (e.g. "room.delete")
    resource: str | None = None  # target resource (e.g. room name, file path)
    role: str | None = None      # actor's role: "human" | "agent" | "admin"
    tenant_id: str | None = None
    extra: dict[str, Any] | None = None  # opaque attribute bag


@dataclass(frozen=True)
class PolicyResult:
    """Outcome of a single evaluation. `decision` always set; `reason` is human text."""

    decision: Decision
    rule_id: str | None
    reason: str
    mode: Mode
    # When mode is SHADOW, `effective_decision` is always ALLOW even if
    # `decision` is DENY/REQUIRE_HUMAN. The engine returns the *would-be*
    # decision in `decision` so the audit log captures intent, while
    # callers gate on `effective_decision`.
    effective_decision: Decision


# ── Canonical action names ──────────────────────────────────────────────────
#
# These are referenced by the routes/services that validate authorization
# before mutating state. We keep them as an explicit allowlist so any future
# Cedar / OPA / Biscuit migration has a single place to enumerate the action
# vocabulary.
#
# Scope categories:
# - room.*    — existing CRUD on rooms (legacy)
# - reflex.*  — reflexd daemon mutations (bid / claim a wake)
# - social.*  — Quorus Social Protocol v1 verbs (Stream A: claim/release/
#               disagree/defer/queue/vote/interrupt). Each maps to a verb
#               on POST /v1/social/{verb}.
# - dm.*      — agent-to-agent direct messages (Stream B).

# Reflex daemon actions — agents can opportunistically bid on a wake task
# and only after winning may they claim it (irrevocably consume it).
_REFLEX_ACTIONS: frozenset[str] = frozenset({
    "reflex:bid",
    "reflex:claim",
})

# Social Protocol v1 verbs as Cedar action names. The verb-set must stay in
# lockstep with quorus/protocol/social_verbs.py::VERBS.
_SOCIAL_VERBS: tuple[str, ...] = (
    "claim", "release", "disagree", "defer", "queue", "vote", "interrupt",
)
_SOCIAL_ACTIONS: frozenset[str] = frozenset(
    f"social:{v}" for v in _SOCIAL_VERBS
)

_DM_ACTIONS: frozenset[str] = frozenset({"dm:agent"})

# Public helper — let callers (route handlers, audit log, tests) ask "is X
# a known reflex / social / dm action?" without re-importing the frozensets.
ALL_REFLEX_ACTIONS: frozenset[str] = _REFLEX_ACTIONS
ALL_SOCIAL_ACTIONS: frozenset[str] = _SOCIAL_ACTIONS
ALL_DM_ACTIONS: frozenset[str] = _DM_ACTIONS

# Actions classified as DESTRUCTIVE — REQUIRE_HUMAN by default for agents.
# Tunable per-tenant via policy override.
_DESTRUCTIVE_ACTIONS = frozenset({
    "room.delete",
    "tenant.delete",
    "agent.delete",
    "audit.purge",
    "secret.read",
    "key.rotate",
    "user.delete",
})


_DEFAULT_POLICY: dict[str, Any] = {
    "mode": "shadow",
    "rules": [],
    "default_decision": "allow",
}


# ── Engine ──────────────────────────────────────────────────────────────────


def _matches(rule: dict[str, Any], ctx: PolicyContext) -> bool:
    """Return True if `rule` matches the evaluation context.

    Match semantics:
    - `action`: exact string match on the canonical verb. Required.
    - `actor`: optional, exact match.
    - `resource`: optional, exact match (no glob in v1).
    - `when.role`: optional role gate.
    """
    if rule.get("action") != ctx.action:
        return False
    if "actor" in rule and rule["actor"] != ctx.actor:
        return False
    if "resource" in rule and rule["resource"] != ctx.resource:
        return False
    when = rule.get("when") or {}
    if "role" in when and when["role"] != ctx.role:
        return False
    return True


def evaluate(ctx: PolicyContext, policy: dict[str, Any] | None = None) -> PolicyResult:
    """Evaluate `ctx` against `policy`. Returns PolicyResult.

    If `policy` is None, uses _DEFAULT_POLICY (shadow mode, no rules,
    default_decision=allow). Destructive actions still get REQUIRE_HUMAN
    via the safe-default fallback when no explicit rule exists.
    """
    p = policy or _DEFAULT_POLICY
    mode = Mode(p.get("mode", "shadow"))

    decision: Decision
    rule_id: str | None = None
    reason = ""

    # 1. Try explicit rule match.
    for i, rule in enumerate(p.get("rules") or []):
        if _matches(rule, ctx):
            try:
                decision = Decision(rule.get("decision", "allow"))
            except ValueError:
                decision = Decision.ALLOW
            rule_id = rule.get("id") or f"rule[{i}]"
            reason = rule.get("reason") or f"matched rule {rule_id}"
            break
    else:
        # 2. Fallback: destructive actions REQUIRE_HUMAN unless explicitly allowed.
        if ctx.action in _DESTRUCTIVE_ACTIONS and (ctx.role or "") == "agent":
            decision = Decision.REQUIRE_HUMAN
            rule_id = "default.destructive"
            reason = (
                f"action '{ctx.action}' is classified destructive — "
                "no explicit rule, agent actor → REQUIRE_HUMAN"
            )
        else:
            try:
                decision = Decision(p.get("default_decision", "allow"))
            except ValueError:
                decision = Decision.ALLOW
            rule_id = "default"
            reason = "no rule matched; default decision applied"

    # 3. Mode shapes effective_decision.
    if mode == Mode.SHADOW:
        effective = Decision.ALLOW  # never block in shadow
    elif mode == Mode.SOFT:
        # SOFT logs and warns, but still allows execution. The warning
        # delivery is the caller's responsibility (post to room, etc).
        effective = Decision.ALLOW
    else:  # HARD
        effective = decision

    return PolicyResult(
        decision=decision,
        rule_id=rule_id,
        reason=reason,
        mode=mode,
        effective_decision=effective,
    )


# ── Loaders ─────────────────────────────────────────────────────────────────


def load_policy_for_tenant(tenant_id: str | None,
                           policy_dir: Path | None = None) -> dict[str, Any]:
    """Load a tenant's policy from `<policy_dir>/<tenant_id>.json`.

    Falls back to `<policy_dir>/_default.json`, then to the built-in
    _DEFAULT_POLICY. Always returns a valid policy dict — never raises.
    Designed to be called from request handlers without a try/except.
    """
    if policy_dir is None:
        return dict(_DEFAULT_POLICY)
    if tenant_id:
        candidate = policy_dir / f"{tenant_id}.json"
        if candidate.exists():
            try:
                return json.loads(candidate.read_text())
            except (json.JSONDecodeError, OSError):
                pass  # fall through to default
    fallback = policy_dir / "_default.json"
    if fallback.exists():
        try:
            return json.loads(fallback.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_POLICY)


def is_destructive(action: str) -> bool:
    """Public helper — used by audit log to flag high-stakes actions."""
    return action in _DESTRUCTIVE_ACTIONS


def is_reflex_action(action: str) -> bool:
    """``reflex:bid`` / ``reflex:claim``."""
    return action in _REFLEX_ACTIONS


def is_social_action(action: str) -> bool:
    """``social:<verb>`` for any verb in the Social Protocol v1 set."""
    return action in _SOCIAL_ACTIONS


def is_dm_action(action: str) -> bool:
    """``dm:agent``."""
    return action in _DM_ACTIONS


# ── Stream-D scoped checks ──────────────────────────────────────────────────
#
# These helpers wrap ``evaluate()`` with the additional constraints flagged
# in the Stream-A → Stream-D handoff:
#
#   - Reflex actions require the actor be a member of the room.
#   - ``social:disagree(blocking)`` is rate-limited to 1×/5min per actor.
#   - ``dm:agent`` requires sender + recipient share a tenant.
#
# Membership and tenant info is plumbed via ``PolicyContext.extra`` because
# the policy engine intentionally does not call into the relay's session
# store. Callers populate these fields from the request handler.


_RATE_LIMIT_LOCK = threading.Lock()
# Map of "<actor>:<action>" -> last-allowed monotonic timestamp.
_RATE_LIMIT_LAST: dict[str, float] = {}

# Default cooldown for blocking-disagree, in seconds. Mirrors the QOD rule
# "blocking = production safety, not style" — 5 minutes between blocking
# disagrees is generous enough for genuine safety calls but rejects flood.
_DISAGREE_BLOCKING_COOLDOWN_S = 5 * 60


def _check_room_membership(ctx: PolicyContext) -> tuple[bool, str]:
    """Return (allowed, reason). Membership comes from ``ctx.extra``."""
    extra = ctx.extra or {}
    members = extra.get("room_members")
    # If the caller did not supply membership, we cannot enforce — fail
    # closed for agent actors, fall through for human/admin.
    if members is None:
        if (ctx.role or "") == "agent":
            return False, "room membership not provided; failing closed for agent"
        return True, "membership unknown but actor is not an agent"
    if isinstance(members, (list, tuple, set, frozenset)):
        if ctx.actor in members:
            return True, "actor is a room member"
        return False, f"actor {ctx.actor!r} is not a member of room {ctx.resource!r}"
    return False, "room_members in extra is not a collection"


def _check_disagree_blocking_rate(ctx: PolicyContext, *,
                                  cooldown_s: int = _DISAGREE_BLOCKING_COOLDOWN_S,
                                  now: float | None = None) -> tuple[bool, str]:
    """Enforce 1 blocking disagree per actor per cooldown window.

    Mode is read from ``ctx.extra["mode"]``. ``"advisory"`` is unbounded.
    """
    extra = ctx.extra or {}
    mode = extra.get("mode")
    if mode != "blocking":
        return True, "advisory disagree — no rate limit"
    key = f"{ctx.actor}:social:disagree:blocking"
    t = now if now is not None else time.monotonic()
    with _RATE_LIMIT_LOCK:
        last = _RATE_LIMIT_LAST.get(key)
        if last is not None and (t - last) < cooldown_s:
            remaining = cooldown_s - (t - last)
            return False, (
                f"blocking disagree rate-limited: "
                f"{remaining:.0f}s remaining in cooldown"
            )
        _RATE_LIMIT_LAST[key] = t
    return True, "within rate limit"


def _check_dm_tenant(ctx: PolicyContext) -> tuple[bool, str]:
    """Sender and recipient must share ``tenant_id``."""
    extra = ctx.extra or {}
    sender_tenant = ctx.tenant_id
    recipient_tenant = extra.get("recipient_tenant_id")
    if not sender_tenant:
        return False, "dm:agent denied — sender tenant_id missing"
    if not recipient_tenant:
        return False, "dm:agent denied — recipient tenant_id missing"
    if sender_tenant != recipient_tenant:
        return False, (
            f"dm:agent denied — cross-tenant "
            f"({sender_tenant!r} → {recipient_tenant!r})"
        )
    return True, "same tenant"


def evaluate_scoped(ctx: PolicyContext,
                    policy: dict[str, Any] | None = None) -> PolicyResult:
    """Evaluate ``ctx`` with the scoped checks layered on top of ``evaluate``.

    This is the entry point routes/social.py, routes/agent_dm.py, and
    scripts/reflexd.py should call when validating reflex / social / dm
    actions. For unrelated actions it is a thin pass-through to
    ``evaluate()`` so callers can use one entry point uniformly.
    """
    base = evaluate(ctx, policy)
    # Only layer scoped checks on top of an otherwise-allowed decision.
    if base.effective_decision != Decision.ALLOW:
        return base

    action = ctx.action

    if is_reflex_action(action) or is_social_action(action):
        ok, reason = _check_room_membership(ctx)
        if not ok:
            return PolicyResult(
                decision=Decision.DENY,
                rule_id="scoped.room_membership",
                reason=reason,
                mode=base.mode,
                effective_decision=(
                    Decision.ALLOW if base.mode == Mode.SHADOW else Decision.DENY
                ),
            )

    if action == "social:disagree":
        ok, reason = _check_disagree_blocking_rate(ctx)
        if not ok:
            return PolicyResult(
                decision=Decision.DENY,
                rule_id="scoped.disagree_blocking_rate",
                reason=reason,
                mode=base.mode,
                effective_decision=(
                    Decision.ALLOW if base.mode == Mode.SHADOW else Decision.DENY
                ),
            )

    if is_dm_action(action):
        ok, reason = _check_dm_tenant(ctx)
        if not ok:
            return PolicyResult(
                decision=Decision.DENY,
                rule_id="scoped.dm_tenant",
                reason=reason,
                mode=base.mode,
                effective_decision=(
                    Decision.ALLOW if base.mode == Mode.SHADOW else Decision.DENY
                ),
            )

    return base


def reset_rate_limits() -> None:
    """Test helper — wipe the in-memory rate-limit ledger between tests."""
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_LAST.clear()
