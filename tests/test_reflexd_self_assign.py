"""Reflexd Phase 2 — Self-Assignment tests.

Covers v2 triage classification, capability tables, capability-aware bid
scoring, the self-assignment preamble, and an end-to-end smoke that an
unrouted ``@open`` message reaches the harness with the preamble injected.

All tests are pure Python — no real subprocess, no real relay. Mocks are
local to each test so the suite runs in well under a second.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

# scripts/ is not a package — same loader pattern as test_reflexd.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFLEXD_PATH = _REPO_ROOT / "scripts" / "reflexd.py"
_spec = importlib.util.spec_from_file_location("reflexd", _REFLEXD_PATH)
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


SELF = "arav-claude"


# ---------------------------------------------------------------------------
# Triage v2 — kind detection
# ---------------------------------------------------------------------------


def test_triage_at_open_returns_open_todo() -> None:
    """``@open <description>`` → kind=open_todo, RESPOND, description carried."""
    res = reflexd.classify_message(
        content="@open implement the new dashboard tile",
        sender="arav",
        self_name=SELF,
    )
    assert res.action == "RESPOND"
    assert res.kind == "open_todo"
    assert "dashboard tile" in res.description
    assert res.role is None


def test_triage_todo_at_role_returns_role_request() -> None:
    """``TODO @<role>: ...`` → kind=role_request, role and desc captured."""
    res = reflexd.classify_message(
        content="TODO @backend: add /v1/work_queue endpoint",
        sender="arav",
        self_name=SELF,
    )
    assert res.action == "RESPOND"
    assert res.kind == "role_request"
    assert res.role == "backend"
    assert "work_queue" in res.description
    # Reason carries the role so the bid log line is greppable.
    assert "backend" in res.reason


def test_triage_who_can_returns_question() -> None:
    """``who can <verb> ...?`` → kind=question (open question to swarm)."""
    res = reflexd.classify_message(
        content="who can investigate this flaky test?",
        sender="arav",
        self_name=SELF,
    )
    assert res.action == "RESPOND"
    assert res.kind == "question"
    # who-can questions get a more specific reason than the trailing-? case
    # so log lines distinguish targeted asks from random questions.
    assert res.reason == "who_can_question"


def test_triage_mention_still_wins_outright() -> None:
    """A literal @-mention beats every other pattern, even if both present."""
    res = reflexd.classify_message(
        content=f"@{SELF} TODO @backend: do the thing",
        sender="arav",
        self_name=SELF,
    )
    assert res.kind == "mention"
    assert res.reason == "literal @mention"


def test_triage_self_message_is_ignored_even_if_at_open() -> None:
    """Self-sent ``@open`` is suppressed — otherwise we'd loop on our own posts."""
    res = reflexd.classify_message(
        content="@open did anyone pick this up",
        sender=SELF,
        self_name=SELF,
    )
    assert res.action == "IGNORE"


# ---------------------------------------------------------------------------
# Capability tables
# ---------------------------------------------------------------------------


def test_capabilities_for_each_harness() -> None:
    """The four canonical capability sets per harness suffix."""
    claude_caps = reflexd.capabilities_for("arav-claude")
    codex_caps = reflexd.capabilities_for("arav-codex")
    gemini_caps = reflexd.capabilities_for("arav-gemini")
    cursor_caps = reflexd.capabilities_for("arav-cursor")

    assert claude_caps == frozenset({"frontend", "react", "tui", "tests", "general"})
    assert codex_caps == frozenset({"backend", "relay", "audit", "policy", "general"})
    assert gemini_caps == frozenset({"docs", "research", "general"})
    assert cursor_caps == frozenset({"refactor", "general"})

    # ``general`` is the floor — every harness owns it so question-style
    # messages always have at least one bidder.
    for caps in (claude_caps, codex_caps, gemini_caps, cursor_caps):
        assert "general" in caps


def test_capabilities_default_unknown_to_claude() -> None:
    """Unknown / human participant → claude defaults (matches detect_harness)."""
    assert reflexd.capabilities_for("alice") == reflexd.CAPABILITIES_CLAUDE


# ---------------------------------------------------------------------------
# Bid scoring
# ---------------------------------------------------------------------------


def test_bid_score_at_mention_is_one_point_oh() -> None:
    """Literal @-mention always bids 1.0 regardless of capability set."""
    bid, reason = reflexd.compute_bid_v2(
        kind="mention", role=None, description="",
        capabilities=reflexd.CAPABILITIES_CURSOR,
        recency_seconds=0.0,
    )
    assert bid == 1.0
    assert reason == "mention"


def test_bid_score_role_match_is_seven_tenths() -> None:
    """``role_request`` with role ∈ caps → bid 0.7."""
    bid, reason = reflexd.compute_bid_v2(
        kind="role_request", role="backend",
        description="add /v1/work_queue endpoint",
        capabilities=reflexd.CAPABILITIES_CODEX,
        recency_seconds=0.0,
    )
    assert bid == pytest.approx(0.7, rel=1e-6)
    assert reason == "role_match:backend"


def test_bid_score_open_todo_capability_match_is_half() -> None:
    """``@open`` with description capability overlap → bid 0.5."""
    bid, reason = reflexd.compute_bid_v2(
        kind="open_todo", role=None,
        description="add new react component for the dashboard",
        capabilities=reflexd.CAPABILITIES_CLAUDE,
        recency_seconds=0.0,
    )
    assert bid == pytest.approx(0.5, rel=1e-6)
    assert reason == "open_capability_match"


def test_bid_score_question_general_is_three_tenths() -> None:
    """``question`` from any general-capable agent → 0.3."""
    bid, reason = reflexd.compute_bid_v2(
        kind="question", role=None, description="",
        capabilities=reflexd.CAPABILITIES_GEMINI,
        recency_seconds=0.0,
    )
    assert bid == pytest.approx(0.3, rel=1e-6)
    assert reason == "general_question"


def test_bid_score_no_capability_match_is_zero() -> None:
    """No capability overlap on any non-mention kind → bid 0.0 (skip)."""
    # role_request to a role this harness doesn't have
    bid_role, _ = reflexd.compute_bid_v2(
        kind="role_request", role="docs",
        description="rewrite the README",
        capabilities=reflexd.CAPABILITIES_CODEX,  # no "docs"
        recency_seconds=0.0,
    )
    assert bid_role == 0.0

    # open_todo whose description names capabilities this harness lacks.
    # ``research`` is in gemini's set; ``backend`` is in codex's; we hand
    # this to cursor (only has refactor + general) and disable the
    # general-fallback so the test is unambiguous.
    bid_open, _ = reflexd.compute_bid_v2(
        kind="open_todo", role=None,
        description="research the new fastapi backend api",
        capabilities=reflexd.CAPABILITIES_CURSOR,
        recency_seconds=0.0,
    )
    assert bid_open == 0.0


def test_bid_score_recency_decays() -> None:
    """Anti-monopoly: recent winner pays 0.1 per second of recency, capped."""
    fresh, _ = reflexd.compute_bid_v2(
        kind="mention", role=None, description="",
        capabilities=reflexd.CAPABILITIES_CLAUDE,
        recency_seconds=0.0,
    )
    decayed, _ = reflexd.compute_bid_v2(
        kind="mention", role=None, description="",
        capabilities=reflexd.CAPABILITIES_CLAUDE,
        recency_seconds=3.0,
    )
    assert decayed < fresh
    assert decayed == pytest.approx(0.7, rel=1e-6)

    # role_request 0.7 - 0.1 * 2.0 = 0.5
    role_decayed, _ = reflexd.compute_bid_v2(
        kind="role_request", role="frontend",
        description="ship the new tile",
        capabilities=reflexd.CAPABILITIES_CLAUDE,
        recency_seconds=2.0,
    )
    assert role_decayed == pytest.approx(0.5, rel=1e-6)

    # Saturates at 0 — never goes negative (BidRequest.ge=0).
    saturated, _ = reflexd.compute_bid_v2(
        kind="question", role=None, description="",
        capabilities=reflexd.CAPABILITIES_CLAUDE,
        recency_seconds=100.0,
    )
    assert saturated == 0.0


# ---------------------------------------------------------------------------
# Self-assignment preamble + prompt wiring
# ---------------------------------------------------------------------------


def test_open_todo_wake_context_has_self_assign_preamble(tmp_path: Path) -> None:
    """When kind=open_todo, _build_prompt prepends the self-assign preamble."""
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="k",
        participant_name=SELF,
        runtime_dir=tmp_path / "rt", log_path=tmp_path / "log",
        spawn_enabled=True,
    )
    cfg.runtime_dir.mkdir()
    daemon = reflexd.Reflexd(cfg)
    envelope = {
        "id": "msg-open",
        "from_name": "arav",
        "room": "quorus-may4-sprint",
        "content": "@open implement the new react dashboard tile",
        "message_type": "chat",
    }
    triage = reflexd.classify_message(
        content=envelope["content"], sender="arav", self_name=SELF,
    )
    assert triage.kind == "open_todo"

    prompt = daemon._build_prompt(envelope, history=[], triage=triage)

    # Preamble must come BEFORE the QOD / wake-intent block — the QOD
    # rule numbering is referenced inside the preamble, so it has to be
    # the first thing the model sees.
    assert prompt.startswith("You picked up an open task")
    assert "react dashboard tile" in prompt
    assert "QOD rule 1" in prompt
    assert "QOD rule 3" in prompt
    # Wake-intent still rendered so transcript + triggering message land.
    assert "Wake Intent" in prompt
    assert "Recent room transcript" in prompt
    assert "@arav:" in prompt


def test_mention_prompt_unchanged_for_phase1(tmp_path: Path) -> None:
    """Phase 1 contract: a literal @-mention prompt does NOT carry the preamble."""
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="k",
        participant_name=SELF,
        runtime_dir=tmp_path / "rt", log_path=tmp_path / "log",
        spawn_enabled=True,
    )
    cfg.runtime_dir.mkdir()
    daemon = reflexd.Reflexd(cfg)
    envelope = {
        "id": "msg-mention",
        "from_name": "arav",
        "room": "r",
        "content": f"@{SELF} ack?",
        "message_type": "chat",
    }
    triage = reflexd.classify_message(
        content=envelope["content"], sender="arav", self_name=SELF,
    )
    prompt = daemon._build_prompt(envelope, history=[], triage=triage)
    # No preamble. Pre-Phase-2 callers (no triage arg) must also be
    # unchanged — verified separately below.
    assert not prompt.startswith("You picked up an open task")
    assert "@-mentioned" in prompt


def test_build_prompt_legacy_no_triage_still_works(tmp_path: Path) -> None:
    """Backward compat: callers that don't pass ``triage`` keep the old shape."""
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="k",
        participant_name=SELF,
        runtime_dir=tmp_path / "rt", log_path=tmp_path / "log",
        spawn_enabled=True,
    )
    cfg.runtime_dir.mkdir()
    daemon = reflexd.Reflexd(cfg)
    envelope = {
        "id": "msg-legacy", "from_name": "arav", "room": "r",
        "content": f"@{SELF} ack?", "message_type": "chat",
    }
    prompt = daemon._build_prompt(envelope, history=[])
    assert "Wake Intent" in prompt
    assert "@arav:" in prompt


# ---------------------------------------------------------------------------
# E2E smoke — extends FakeRelay so a no-target ``@open`` reaches the adapter
# ---------------------------------------------------------------------------


class FakeRelay:
    """Mirrors test_reflexd.FakeRelay but exposes posts for assertion."""

    def __init__(self, *, sse_payload: bytes, winner: str = SELF) -> None:
        self.sse_payload = sse_payload
        self.winner = winner
        self.bid_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/auth/token":
            return httpx.Response(200, json={"token": "fake-jwt"})
        if path == "/stream/token":
            return httpx.Response(200, json={"token": "stream-token"})
        if path.startswith("/stream/"):
            return httpx.Response(
                200, content=self.sse_payload,
                headers={"content-type": "text/event-stream"},
            )
        if path == "/v1/bid":
            body = json.loads(request.content)
            self.bid_calls.append(body)
            return httpx.Response(200, json={
                "accepted": True, "leader": body["participant"],
                "leader_bid": body["bid"],
                "window_expires_at": "2026-05-02T12:00:05+00:00",
                "fairness_credit": 0.0,
            })
        if path == "/v1/claim":
            body = json.loads(request.content)
            self.claim_calls.append(body)
            return httpx.Response(200, json={
                "claimed": True, "winner": self.winner, "bid": 0.5,
                "claim_token": "tok",
                "expires_at": "2026-05-02T12:00:10+00:00",
                "candidates": [self.winner], "fairness_credit": {self.winner: -1.0},
            })
        if path.endswith("/messages"):
            body = json.loads(request.content)
            self.posts.append({"path": path, **body})
            return httpx.Response(200, json={"id": "msg-out", "status": "ok"})
        if "/history" in path:
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(404, json={"detail": f"unexpected {path}"})


def _sse_message_event(envelope: dict[str, Any]) -> bytes:
    return (
        f"event: connected\ndata: {{\"participant\":\"{SELF}\"}}\n\n"
        f"event: message\ndata: {json.dumps(envelope)}\n\n"
    ).encode()


def test_e2e_open_todo_via_smoke_relay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No @-mention. ``@open`` with frontend keywords routes to claude.

    The harness is a stub; we assert that:
      - reflexd posted exactly one bid
      - the bid reason matches the open-capability path (NOT @mention)
      - the bid value is ~0.5 (open_todo + capability match)
      - the harness saw a context that started with the self-assign preamble
    """
    envelope = {
        "id": "msg-open-1",
        "from_name": "arav",
        "to": "anyone",
        "room": "quorus-may4-sprint",
        "content": "@open ship a new react settings tile in the TUI",
        "message_type": "chat",
        "timestamp": "2026-05-02T12:00:00+00:00",
    }
    fake = FakeRelay(sse_payload=_sse_message_event(envelope))

    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="mct_test_secret",
        participant_name=SELF,
        runtime_dir=tmp_path / "runtime", log_path=tmp_path / "reflexd.log",
        spawn_enabled=True,
    )
    cfg.runtime_dir.mkdir()

    seen_contexts: list[str] = []

    class StubAdapter:
        async def run(self, harness: str, *, context: str) -> str:
            assert harness == "claude"
            seen_contexts.append(context)
            return "[ack-stub] picking up the open task"

    daemon = reflexd.Reflexd(cfg, adapter=StubAdapter())  # type: ignore[arg-type]

    async def go() -> None:
        async with reflexd.RelayClient(
            relay_url=cfg.relay_url, api_key=cfg.api_key,
        ) as relay:
            await relay._client.aclose()  # type: ignore[union-attr]
            relay._client = httpx.AsyncClient(
                base_url=cfg.relay_url,
                transport=fake.transport(),
                follow_redirects=True,
            )
            monkeypatch.setattr(reflexd, "BID_WINDOW_SECONDS", 0.5)
            handled = await daemon.handle_room_message(relay, envelope)
            assert handled is True

    asyncio.run(go())

    assert len(fake.bid_calls) == 1
    bid_call = fake.bid_calls[0]
    # Open-todo + react/tui keywords vs claude caps → 0.5 bid.
    assert bid_call["bid"] == pytest.approx(0.5, rel=1e-6)
    # The reason on the wire identifies WHY we bid — not "literal @mention".
    assert bid_call["reason"] == "open_capability_match"

    assert len(fake.posts) == 1
    assert "[ack-stub]" in fake.posts[0]["content"]

    # Crucially: the harness's context began with the self-assign preamble.
    assert seen_contexts, "harness was never invoked"
    ctx = seen_contexts[0]
    assert ctx.startswith("You picked up an open task")
    assert "QOD rule 1" in ctx
    assert "QOD rule 3" in ctx


def test_e2e_open_todo_no_capability_match_skips_bid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Negative path: gemini sees a backend @open and does NOT bid.

    Proves the capability filter actually fires — without this guard,
    the daemon would still POST a 0.0 bid and pollute the auction.
    """
    envelope = {
        "id": "msg-open-2",
        "from_name": "arav",
        "to": "anyone",
        "room": "r",
        "content": "@open add a new fastapi backend audit endpoint",
        "message_type": "chat",
    }
    fake = FakeRelay(sse_payload=_sse_message_event(envelope))

    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="mct_test_secret",
        participant_name="arav-gemini",
        runtime_dir=tmp_path / "runtime", log_path=tmp_path / "reflexd.log",
        spawn_enabled=False,
    )
    cfg.runtime_dir.mkdir()

    daemon = reflexd.Reflexd(cfg)

    async def go() -> None:
        async with reflexd.RelayClient(
            relay_url=cfg.relay_url, api_key=cfg.api_key,
        ) as relay:
            await relay._client.aclose()  # type: ignore[union-attr]
            relay._client = httpx.AsyncClient(
                base_url=cfg.relay_url,
                transport=fake.transport(),
                follow_redirects=True,
            )
            handled = await daemon.handle_room_message(relay, envelope)
            assert handled is False  # no bid POSTed

    asyncio.run(go())
    assert fake.bid_calls == []
    assert fake.posts == []


def test_role_request_routes_to_matching_harness() -> None:
    """``TODO @backend: ...`` scores high for codex, zero for claude.

    Sanity check the canonical example from the spec — proves the
    classifier + capability table + scoring all line up end-to-end.
    """
    msg = "TODO @backend: ship the /v1/work_queue endpoint"
    triage = reflexd.classify_message(
        content=msg, sender="arav", self_name=SELF,
    )
    assert triage.kind == "role_request" and triage.role == "backend"

    bid_codex, _ = reflexd.compute_bid_v2(
        kind=triage.kind, role=triage.role,
        description=triage.description,
        capabilities=reflexd.CAPABILITIES_CODEX,
        recency_seconds=0.0,
    )
    bid_claude, _ = reflexd.compute_bid_v2(
        kind=triage.kind, role=triage.role,
        description=triage.description,
        capabilities=reflexd.CAPABILITIES_CLAUDE,
        recency_seconds=0.0,
    )
    assert bid_codex == pytest.approx(0.7)
    assert bid_claude == 0.0


def test_at_open_routes_to_claude_for_react_work() -> None:
    """The example from the spec: ``@open implement react X`` → claude wins.

    This is the *demo* case — a no-target broadcast that lands with the
    right specialist purely on capability match. We check that:
      - all four harnesses see the same triage classification,
      - claude's bid (frontend/react in caps) > 0,
      - codex / gemini / cursor either bid lower or don't bid at all.
    """
    msg = "@open implement the new react component with the latest test fixture"
    triage = reflexd.classify_message(
        content=msg, sender="arav", self_name=SELF,
    )
    assert triage.kind == "open_todo"

    bids = {
        harness: reflexd.compute_bid_v2(
            kind=triage.kind, role=triage.role,
            description=triage.description,
            capabilities=reflexd.capabilities_for(f"agent-{harness}"),
            recency_seconds=0.0,
        )[0]
        for harness in ("claude", "codex", "gemini", "cursor")
    }
    # Claude has both react and tests → strict winner.
    assert bids["claude"] == pytest.approx(0.5)
    # Codex / cursor / gemini have neither react nor tests — bid 0.0.
    assert bids["codex"] == 0.0
    assert bids["gemini"] == 0.0
    assert bids["cursor"] == 0.0
    # Sanity: claude is the unique max.
    assert max(bids, key=bids.get) == "claude"
