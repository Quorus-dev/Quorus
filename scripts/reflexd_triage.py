"""Reflexd triage v2 — pattern detection, capability matching, bid scoring.

Phase 2: Self-Assignment. Extends the v0 triage in :mod:`reflexd` so the
daemon can detect *open work* in chat and bid on it without being explicitly
@-mentioned. Pure functions only — zero IO, zero relay coupling — so unit
tests can call them directly without spinning a daemon.

The four ``kind`` values represent strictly more specific routing signals:

============  =========================================================
``mention``   Literal ``@self_name`` token. Highest-priority — pre-Phase-2
              behaviour. Bid 1.0.
``question``  Trailing ``?`` or ``who can <verb> ...?``. Bid 0.3 if the
              agent has the ``general`` capability, else skip.
``open_todo`` ``@open <description>`` — broadcast TODO to the whole
              swarm. Bid 0.5 when the description's keywords overlap the
              agent's capability set; skip otherwise.
``role_request``  ``TODO @<role>: <desc>`` — role-tagged work. Bid 0.7
              when ``<role>`` ∈ capabilities. Highest-confidence
              auto-claim signal short of a literal mention.
============  =========================================================

Capability tables map participant-name suffix → fixed capability set.
Tables live here (not in reflexd.py) so future Phase-2.5 work — registry
file, per-room overrides — can extend them without touching the daemon
core. Keep these tables conservative: every additional capability raises
the agent's bid surface and risks contention.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Capability table — derived from harness suffix (matches detect_harness)
# ---------------------------------------------------------------------------
# Each entry is the *fixed* set we ship with — Phase 2.5 may augment it from
# a registry file. ``general`` is the catch-all bucket every harness owns
# so question-style messages can still get *some* responder.

CAPABILITIES_CLAUDE: frozenset[str] = frozenset({
    "frontend", "react", "tui", "tests", "general",
})
CAPABILITIES_CODEX: frozenset[str] = frozenset({
    "backend", "relay", "audit", "policy", "general",
})
CAPABILITIES_GEMINI: frozenset[str] = frozenset({
    "docs", "research", "general",
})
CAPABILITIES_CURSOR: frozenset[str] = frozenset({
    "refactor", "general",
})
# Wave-7: Opencode is multi-provider (75+ LLM providers via opencode.ai). It
# has no fixed model bias, so we tag it ``general`` only — let bidding fall
# back to mention-priority. Same for Cline, which is preview-only.
CAPABILITIES_OPENCODE: frozenset[str] = frozenset({"general"})
CAPABILITIES_CLINE: frozenset[str] = frozenset({"general"})

_HARNESS_CAPABILITIES: dict[str, frozenset[str]] = {
    "claude": CAPABILITIES_CLAUDE,
    "codex": CAPABILITIES_CODEX,
    "gemini": CAPABILITIES_GEMINI,
    "cursor": CAPABILITIES_CURSOR,
    "opencode": CAPABILITIES_OPENCODE,
    "cline": CAPABILITIES_CLINE,
}


# ---------------------------------------------------------------------------
# Triage result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageResult:
    """v2 triage output.

    ``action``: "RESPOND" or "IGNORE" — same contract as v0 ``triage_local``.
    ``reason``: short human-readable label, used in bid metadata + logs.
    ``kind``:   one of {"mention", "question", "open_todo", "role_request",
                "social_verb", "none"}. ``"none"`` ↔ ``action == "IGNORE"``
                except for ``social_verb`` which is also IGNORE — see below.
    ``role``:   role token from ``role_request`` (e.g. "frontend"). ``None``
                for other kinds.
    ``description``: the open-work description for ``open_todo`` /
                ``role_request`` so capability matching has something to
                grep. Empty string for other kinds.
    ``verb``:   social-protocol verb when ``kind == 'social_verb'``. The
                relay's social state-machine handles state mutations from
                wire-typed verbs; reflexd intentionally abstains so prose
                replies don't add noise to a structured handoff.
    """

    action: str
    reason: str
    kind: str = "none"
    role: str | None = None
    description: str = ""
    verb: str | None = None


# ---------------------------------------------------------------------------
# Pattern detectors — pure, no side effects
# ---------------------------------------------------------------------------

# ``@open <something>`` — broadcast TODO. The ``\b`` after ``open`` rejects
# tokens like ``@opensesame`` from matching.
_OPEN_RE = re.compile(r"@open\b\s*(.*)", re.IGNORECASE)

# ``TODO @<role>: <description>`` — role-tagged work. Role must be a bare
# word (alphanum + dash/underscore), otherwise we'd accept email addresses.
_ROLE_TODO_RE = re.compile(
    r"\bTODO\s*@([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.+)$",
    re.IGNORECASE,
)

# ``who can <verb> ...?`` — open-question pattern. Trailing ``?`` is
# required; without it we'd over-match prose like "who can know for sure".
_WHO_CAN_RE = re.compile(
    r"\bwho\s+can\s+\S+.*\?\s*$",
    re.IGNORECASE,
)


# Social Protocol v1 verb prefixes — a leading "/<verb>" or a JSON envelope
# with kind=="social". When detected, the relay's social state-machine owns
# the response; reflexd abstains.
_SOCIAL_VERBS: tuple[str, ...] = (
    "claim", "release", "disagree", "defer", "queue", "vote", "interrupt",
)
_VERB_PREFIXES: tuple[str, ...] = tuple(f"/{v}" for v in _SOCIAL_VERBS)


def detect_social_verb(content: str) -> tuple[str, str] | None:
    """Detect Quorus Social Protocol v1 verbs in *content*.

    Recognizes two forms::

        "/disagree blocking ..."  → ("disagree", "blocking ...")
        '{"kind":"social","verb":"defer", ...}' → ("defer", "")

    Returns ``(verb, suffix)`` on a hit, ``None`` otherwise. Pure function —
    no allocation outside the regex/json branch when no verb is detected.
    """
    if not content:
        return None
    text = content.strip()
    for prefix in _VERB_PREFIXES:
        if text.startswith(prefix) and (
            len(text) == len(prefix) or text[len(prefix)].isspace()
        ):
            return prefix[1:], text[len(prefix):].strip()
    if text.startswith("{") and '"kind":"social"' in text.replace(" ", ""):
        try:
            import json as _json
            data = _json.loads(text)
        except (ValueError, _json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        verb = data.get("verb")
        if isinstance(verb, str) and verb in _SOCIAL_VERBS:
            return verb, ""
    return None


def has_outstanding_defer(
    *, defer_graph: dict, target: str, source: str, now: float,
) -> bool:
    """True iff ``source`` has an unexpired ``defer(to=target)`` edge.

    Pure function — reads a snapshot of the relay's defer-graph (e.g. the
    one returned by ``GET /v1/social/state/{room_id}``) and answers whether
    ``source`` is currently deferring to ``target``.
    """
    edges = defer_graph.get(source, {}) if isinstance(defer_graph, dict) else {}
    expires = edges.get(target) if isinstance(edges, dict) else None
    return expires is not None and float(expires) > now


def _has_literal_mention(text: str, self_name: str) -> bool:
    """True iff ``text`` contains ``@self_name`` as a whole token."""
    return bool(re.search(rf"@{re.escape(self_name)}(?![A-Za-z0-9_-])", text))


def classify_message(
    *,
    content: str,
    sender: str | None,
    self_name: str,
    message_type: str = "chat",
) -> TriageResult:
    """Classify a chat envelope into a :class:`TriageResult`.

    Order of precedence (most-specific first):
      1. literal ``@self_name`` → ``kind="mention"`` (Phase 1 behaviour)
      2. ``TODO @<role>: ...``  → ``kind="role_request"``
      3. ``@open ...``          → ``kind="open_todo"``
      4. ``who can <verb> ...?`` → ``kind="question"`` (open question)
      5. trailing ``?``         → ``kind="question"`` (catch-all question)
      6. otherwise              → IGNORE

    Self-sent messages and non-conversational types short-circuit to
    IGNORE before any pattern matching runs — so the daemon can never
    bid on its own broadcasts (which would loop).
    """
    if sender and sender == self_name:
        return TriageResult("IGNORE", "self message")
    if message_type == "social":
        # Wire-typed social verbs are handled by the relay state-machine, not
        # reflexd. Surface the verb in the result so the daemon can log it,
        # but do not bid a prose reply on top of structured coordination.
        verb_match = detect_social_verb(content or "")
        if verb_match is not None:
            return TriageResult(
                "IGNORE",
                f"social_verb:{verb_match[0]}",
                kind="social_verb",
                verb=verb_match[0],
            )
        return TriageResult("IGNORE", "social_message_type_no_verb")
    if message_type not in {"chat", "request", "question"}:
        return TriageResult("IGNORE", f"non-conversational type {message_type!r}")

    text = content or ""

    # 0. Verb-prefixed prose ("/disagree blocking ...") in a chat-typed
    #    message — same abstention as the message_type=="social" branch.
    verb_match = detect_social_verb(text)
    if verb_match is not None:
        return TriageResult(
            "IGNORE",
            f"social_verb:{verb_match[0]}",
            kind="social_verb",
            verb=verb_match[0],
        )

    # 1. Literal @-mention wins outright — preserve Phase 1 wire reason.
    if _has_literal_mention(text, self_name):
        return TriageResult("RESPOND", "literal @mention", kind="mention")

    # 2. Role-tagged TODO. Match before @open so ``TODO @backend:`` doesn't
    #    fall through to the question heuristic.
    role_match = _ROLE_TODO_RE.search(text)
    if role_match:
        role = role_match.group(1).strip().lower()
        desc = role_match.group(2).strip()
        return TriageResult(
            "RESPOND",
            f"role_request:{role}",
            kind="role_request",
            role=role,
            description=desc,
        )

    # 3. ``@open ...``. The literal text after ``@open`` is the description
    #    we use for capability matching.
    open_match = _OPEN_RE.search(text)
    if open_match:
        desc = open_match.group(1).strip()
        return TriageResult(
            "RESPOND",
            "open_todo",
            kind="open_todo",
            description=desc,
        )

    # 4. ``who can <verb> ...?`` — open question. Counts as an
    #    ``open question`` because it's directed at the swarm, not a
    #    specific participant.
    if _WHO_CAN_RE.search(text):
        return TriageResult("RESPOND", "who_can_question", kind="question")

    # 5. Generic trailing question mark. Lower confidence than ``who can``.
    if text.rstrip().endswith("?"):
        return TriageResult("RESPOND", "question mark", kind="question")

    return TriageResult("IGNORE", "no signal")


# ---------------------------------------------------------------------------
# Capability lookup
# ---------------------------------------------------------------------------


def capabilities_for(participant: str, *, harness: str | None = None) -> frozenset[str]:
    """Return the capability set for ``participant``.

    If ``harness`` is given (e.g. result of :func:`reflexd.detect_harness`)
    we look it up directly. Otherwise we apply the same suffix heuristic
    as ``detect_harness``: ``-claude``/``-codex``/``-gemini``/``-cursor``.
    Default is the claude set (matches ``detect_harness`` fallback).
    """
    if harness is not None:
        return _HARNESS_CAPABILITIES.get(harness, CAPABILITIES_CLAUDE)
    name = participant.lower()
    if name.endswith("-codex") or "-codex-" in name:
        return CAPABILITIES_CODEX
    if name.endswith("-gemini") or "-gemini-" in name:
        return CAPABILITIES_GEMINI
    if name.endswith("-cursor") or "-cursor-" in name:
        return CAPABILITIES_CURSOR
    if name.endswith("-claude") or "-claude-" in name:
        return CAPABILITIES_CLAUDE
    if name.endswith("-opencode") or "-opencode-" in name:
        return CAPABILITIES_OPENCODE
    if name.endswith("-cline") or "-cline-" in name:
        return CAPABILITIES_CLINE
    # Substring fallback before the claude default so a participant called
    # ``my-codex-machine`` still gets codex caps.
    for needle, caps in (
        ("codex", CAPABILITIES_CODEX),
        ("gemini", CAPABILITIES_GEMINI),
        ("cursor", CAPABILITIES_CURSOR),
        ("opencode", CAPABILITIES_OPENCODE),
        ("cline", CAPABILITIES_CLINE),
        ("claude", CAPABILITIES_CLAUDE),
    ):
        if needle in name:
            return caps
    return CAPABILITIES_CLAUDE


# ---------------------------------------------------------------------------
# Capability ↔ description matching
# ---------------------------------------------------------------------------

# Keyword → canonical capability mapping. Conservative: only adds a
# capability tag if a synonym appears in the description. ``general`` is
# *not* triggered here because every agent already has it.
_CAPABILITY_KEYWORDS: dict[str, str] = {
    # frontend / react / tui
    "frontend": "frontend", "ui": "frontend",
    "react": "react", "jsx": "react", "tsx": "react", "component": "react",
    "tui": "tui", "terminal": "tui", "textual": "tui",
    "test": "tests", "tests": "tests", "pytest": "tests",
    "regression": "tests", "fixture": "tests",
    # backend / relay / audit / policy
    "backend": "backend", "api": "backend", "endpoint": "backend",
    "relay": "relay", "fastapi": "backend",
    "audit": "audit", "auditing": "audit", "hash-chain": "audit",
    "policy": "policy", "policies": "policy", "rbac": "policy",
    # docs / research
    "docs": "docs", "documentation": "docs", "readme": "docs",
    "research": "research", "investigate": "research", "explore": "research",
    # refactor
    "refactor": "refactor", "rename": "refactor", "cleanup": "refactor",
}


def _description_capabilities(description: str) -> set[str]:
    """Return capabilities implied by keywords in ``description``."""
    if not description:
        return set()
    text = description.lower()
    found: set[str] = set()
    # Boundary-anchored search keeps "test" from matching "latest".
    for kw, cap in _CAPABILITY_KEYWORDS.items():
        if re.search(rf"\b{re.escape(kw)}\b", text):
            found.add(cap)
    return found


def matches_capability(
    *,
    description: str,
    capabilities: frozenset[str] | set[str],
    fallback_general: bool = True,
) -> bool:
    """True iff ``description``'s implied capabilities overlap.

    ``fallback_general``: when True (the default), a participant with the
    ``general`` capability matches *any* open work whose description
    yielded no specific capability hits — i.e. the open task is too vague
    to route, so anyone with general can step in.
    """
    needs = _description_capabilities(description)
    if not needs:
        return fallback_general and "general" in capabilities
    return bool(needs & capabilities)


# ---------------------------------------------------------------------------
# Bid scoring
# ---------------------------------------------------------------------------

# Anti-monopoly recency penalty: the agent that just won loses 0.1 of bid
# per second since its last win, so a fresh idle agent edges it out on
# the next open task. Mirror compute_bid() in reflexd.py.
_RECENCY_PENALTY_PER_SEC = 0.1
_RECENCY_PENALTY_CAP = 5.0  # seconds — beyond this we stop subtracting


def compute_bid_v2(
    *,
    kind: str,
    role: str | None,
    description: str,
    capabilities: frozenset[str] | set[str],
    recency_seconds: float,
) -> tuple[float, str]:
    """Score a bid for a Phase-2 triage classification.

    Returns ``(bid, reason)`` where ``bid == 0.0`` means "do not bid"
    and ``reason`` is a short label safe for log lines.

    Scoring rubric (matches the spec):

    * ``mention``        → 1.0 (always bid)
    * ``role_request``   → 0.7 if ``role`` ∈ capabilities, else 0.0
    * ``open_todo``      → 0.5 if description capabilities overlap, else 0.0
    * ``question``       → 0.3 if "general" ∈ capabilities, else 0.0
    * unknown kind       → 0.0

    Recency penalty applies to all non-zero base scores; the saturation
    cap matches v0 behaviour so a long-idle agent never goes negative.
    """
    base, label = _base_bid(
        kind=kind,
        role=role,
        description=description,
        capabilities=capabilities,
    )
    if base == 0.0:
        return 0.0, label
    penalty = _RECENCY_PENALTY_PER_SEC * max(0.0, min(recency_seconds, _RECENCY_PENALTY_CAP))
    return max(0.0, min(1.0, base - penalty)), label


def _base_bid(
    *,
    kind: str,
    role: str | None,
    description: str,
    capabilities: frozenset[str] | set[str],
) -> tuple[float, str]:
    """Internal helper: pre-recency score + reason label."""
    if kind == "mention":
        return 1.0, "mention"
    if kind == "role_request":
        if role and role in capabilities:
            return 0.7, f"role_match:{role}"
        return 0.0, "role_no_match"
    if kind == "open_todo":
        if matches_capability(description=description, capabilities=capabilities):
            return 0.5, "open_capability_match"
        return 0.0, "open_no_capability"
    if kind == "question":
        if "general" in capabilities:
            return 0.3, "general_question"
        return 0.0, "question_no_general"
    return 0.0, "unknown_kind"


# ---------------------------------------------------------------------------
# Self-assignment preamble
# ---------------------------------------------------------------------------


def self_assign_preamble(*, description: str) -> str:
    """Return the preamble injected when reflexd auto-claims an open task.

    The wake-context is then appended below this preamble — see
    ``Reflexd._build_prompt``. The wording is deliberately operational so
    the harness's reply matches QOD rules 1 and 3 (post a plan, then post
    a ✅ ship line on completion) without the agent having to remember.
    """
    desc = (description or "").strip() or "(no description provided)"
    return (
        f'You picked up an open task: "{desc}"\n'
        "Begin by posting a 1-line plan to the room (QOD rule 1).\n"
        "Then work on it. After commit, post ✅ with what changed (QOD rule 3).\n"
    )
