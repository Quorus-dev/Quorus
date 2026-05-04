"""Tests for ``scripts/reflexd.py`` — PR-C1 client-side daemon."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

# scripts/ is not a package — load reflexd by path so pytest can import it.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFLEXD_PATH = _REPO_ROOT / "scripts" / "reflexd.py"
_spec = importlib.util.spec_from_file_location("reflexd", _REFLEXD_PATH)
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd  # patches reach the loaded module
_spec.loader.exec_module(reflexd)


SELF = "arav-claude"


# ---------------------------------------------------------------------------
# Pure-helper unit tests (no relay needed)
# ---------------------------------------------------------------------------


def test_triage_at_mention_returns_RESPOND() -> None:
    action, reason = reflexd.triage_local(
        content="@arav-claude can you check this?",
        sender="arav",
        self_name=SELF,
    )
    assert action == "RESPOND"
    assert "mention" in reason.lower()


def test_triage_at_mention_substring_does_not_match() -> None:
    # @arav-claude-foo should NOT count as @arav-claude
    action, _ = reflexd.triage_local(
        content="hey @arav-claude-foo what about it?",
        sender="arav",
        self_name=SELF,
    )
    # The trailing "?" still triggers question-mark heuristic.
    assert action == "RESPOND"
    # But not for a non-mention question without trailing ?
    action2, reason2 = reflexd.triage_local(
        content="@arav-claude-foo did something",
        sender="arav",
        self_name=SELF,
    )
    assert action2 == "IGNORE", reason2


def test_triage_question_returns_RESPOND() -> None:
    action, reason = reflexd.triage_local(
        content="what should we do here?",
        sender="arav",
        self_name=SELF,
    )
    assert action == "RESPOND"
    assert "question" in reason.lower()


def test_triage_self_message_returns_IGNORE() -> None:
    action, reason = reflexd.triage_local(
        content="@arav-claude let me think",
        sender=SELF,
        self_name=SELF,
    )
    assert action == "IGNORE"
    assert "self" in reason.lower()


def test_triage_non_conversational_returns_IGNORE() -> None:
    action, _ = reflexd.triage_local(
        content="@arav-claude please?",
        sender="arav",
        self_name=SELF,
        message_type="status",
    )
    assert action == "IGNORE"


def test_detect_social_verb_recognizes_slash_prefix() -> None:
    """`/disagree blocking ...` is a wire-typed verb prefix."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "reflexd_triage",
        _REPO_ROOT / "scripts" / "reflexd_triage.py",
    )
    assert spec is not None and spec.loader is not None
    triage = _ilu.module_from_spec(spec)
    spec.loader.exec_module(triage)

    assert triage.detect_social_verb("/disagree blocking wrong path") == (
        "disagree", "blocking wrong path",
    )
    assert triage.detect_social_verb("/claim t-1 60 ship") == (
        "claim", "t-1 60 ship",
    )
    # Non-verb shapes return None.
    assert triage.detect_social_verb("hello world") is None
    assert triage.detect_social_verb("/joinsomeroom") is None  # no boundary


def test_detect_social_verb_recognizes_json_envelope() -> None:
    """A wire envelope JSON body counts as a verb hit."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "reflexd_triage",
        _REPO_ROOT / "scripts" / "reflexd_triage.py",
    )
    assert spec is not None and spec.loader is not None
    triage = _ilu.module_from_spec(spec)
    spec.loader.exec_module(triage)

    envelope = (
        '{"kind":"social","verb":"defer","actor":"alice","room_id":"r1",'
        '"ts":"2026-05-03T08:00:00Z","payload":{"to":"bob"}}'
    )
    hit = triage.detect_social_verb(envelope)
    assert hit is not None
    assert hit[0] == "defer"


def test_classify_message_short_circuits_verb_prefix() -> None:
    """A `/release ...` chat must IGNORE — relay state-machine handles it."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "reflexd_triage",
        _REPO_ROOT / "scripts" / "reflexd_triage.py",
    )
    assert spec is not None and spec.loader is not None
    triage = _ilu.module_from_spec(spec)
    spec.loader.exec_module(triage)

    result = triage.classify_message(
        content="/release t1 finished",
        sender="bob",
        self_name="arav-claude",
        message_type="chat",
    )
    assert result.action == "IGNORE"
    assert result.kind == "social_verb"
    assert result.verb == "release"

    # message_type=='social' with a JSON envelope also short-circuits.
    envelope = (
        '{"kind":"social","verb":"vote","actor":"bob","room_id":"r1",'
        '"ts":"2026-05-03T08:00:00Z","payload":{"option":"approve"}}'
    )
    result2 = triage.classify_message(
        content=envelope,
        sender="bob",
        self_name="arav-claude",
        message_type="social",
    )
    assert result2.action == "IGNORE"
    assert result2.kind == "social_verb"
    assert result2.verb == "vote"


def test_busyfile_skips_wake(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    assert reflexd.is_busy(SELF, runtime) is False
    # Drive setup through the shared TurnGuard helper so reflexd reads what
    # the harness writers actually produce in production.
    from quorus.runtime import turnguard as _tg
    _tg.begin(SELF, tool="Bash", ttl=60, dir_override=runtime)
    assert reflexd.is_busy(SELF, runtime) is True
    _tg.end(SELF, dir_override=runtime)
    assert reflexd.is_busy(SELF, runtime) is False


def test_bid_calculation_decays_with_recency() -> None:
    # Mention beats question.
    fresh_mention = reflexd.compute_bid(is_mention=True, recency_seconds=0)
    fresh_question = reflexd.compute_bid(is_mention=False, recency_seconds=0)
    assert fresh_mention == 1.0
    assert fresh_question == 0.5

    # Recency penalty kicks in.
    decayed = reflexd.compute_bid(is_mention=True, recency_seconds=3)
    assert decayed == pytest.approx(0.7, rel=1e-6)
    assert decayed < fresh_mention

    # Saturates at 0 (won't break BidRequest's ge=0 contract).
    very_decayed = reflexd.compute_bid(is_mention=False, recency_seconds=100)
    assert very_decayed == 0.0


def test_safe_message_preview_truncates_and_flattens() -> None:
    out = reflexd.safe_message_preview("a" * 200)
    assert len(out) <= reflexd.MENTION_PREVIEW_CHARS
    multi = reflexd.safe_message_preview("hello\nworld\rfoo")
    assert "\n" not in multi
    assert "\r" not in multi


# ---------------------------------------------------------------------------
# CRIT-5: PII guard for log lines that mention chat content
# ---------------------------------------------------------------------------


def test_safe_log_summary_excludes_raw_content() -> None:
    """CRIT-5: ``safe_log_summary`` MUST never return the raw body.

    The default mode emits ``len=N hash=XXXXXXXX`` so two log lines for
    the same chat body are correlatable, but the body itself never
    lands on disk in ``~/.quorus/reflexd.log``.
    """
    body = "patient health card 1234567890 dob 1990-01-01"
    summary = reflexd.safe_log_summary(body)
    # No literal substring of the body survives.
    assert "patient" not in summary
    assert "1234567890" not in summary
    assert "1990-01-01" not in summary
    # Format pinned: len=N hash=XXXXXXXX.
    assert summary.startswith("len=")
    assert "hash=" in summary
    # Hash field is 8 hex chars.
    digest = summary.split("hash=", 1)[1]
    assert len(digest) == 8
    assert all(c in "0123456789abcdef" for c in digest)


def test_safe_log_summary_stable_hash_for_same_input() -> None:
    """Two summaries for the same body emit the same hash — correlation key."""
    body = "ack — your task-#42 is queued"
    s1 = reflexd.safe_log_summary(body)
    s2 = reflexd.safe_log_summary(body)
    assert s1 == s2


def test_safe_log_summary_debug_redacts_pii() -> None:
    """Debug mode adds a redacted preview — no raw email / phone / @-mention."""
    body = "ping arav@example.com call +1 555-123-4567 cc @alice please"
    summary = reflexd.safe_log_summary(body, debug=True)
    # Original PII is gone.
    assert "arav@example.com" not in summary
    assert "+1 555-123-4567" not in summary
    assert "@alice" not in summary
    # Redaction tokens present (in at least one form).
    assert "<redacted-email>" in summary or "<redacted-mention>" in summary


def test_reflexd_log_no_content_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    """The triage log emits ``len=`` + ``hash=``, never raw content.

    This is the regression for CRIT-5. We force a real subscriber path
    by calling ``safe_log_summary`` the same way the daemon does at the
    triage and DM log sites.
    """
    body = "secret PHI never log this"
    summary = reflexd.safe_log_summary(body)
    assert "secret" not in summary
    assert "PHI" not in summary
    assert "len=" in summary
    assert "hash=" in summary


# ---------------------------------------------------------------------------
# CRIT-7: subprocess argv injection guard
# ---------------------------------------------------------------------------


def test_subprocess_argv_includes_separator_for_claude() -> None:
    """``--`` separator present so leading-dash content is not parsed as a flag."""
    argv = reflexd.build_claude_argv("-flag-style-input")
    assert "--" in argv
    # The dash-prefixed payload comes after ``--``.
    assert argv.index("--") < argv.index("-flag-style-input")


def test_subprocess_argv_includes_separator_for_codex() -> None:
    argv = reflexd.build_codex_argv("-flag-style-input")
    assert "--" in argv
    assert argv.index("--") < argv.index("-flag-style-input")


def test_subprocess_argv_uses_equals_form_for_gemini() -> None:
    """Gemini ``--prompt=<ctx>`` form keeps the value attached to the flag.

    The parser would re-interpret ``--prompt -- --version`` as the version
    flag, so the only safe form on gemini is ``--prompt=<ctx>``.
    """
    argv = reflexd.build_gemini_argv("-flag-style-input")
    assert any(a.startswith("--prompt=") for a in argv)
    assert "-flag-style-input" in argv[-1]


def test_subprocess_argv_uses_dashdash_form_for_cursor() -> None:
    """Wave-7: Cursor switched from ``--headless --prompt=`` to documented
    ``-p -- <ctx>``. The ``--`` keeps a leading-dash payload positional.
    """
    argv = reflexd.build_cursor_argv("-flag-style-input")
    assert "-p" in argv
    assert "--" in argv
    assert argv.index("--") < argv.index("-flag-style-input")


def test_detect_harness_routes_by_suffix() -> None:
    assert reflexd.detect_harness("arav-claude") == "claude"
    assert reflexd.detect_harness("arav-codex") == "codex"
    assert reflexd.detect_harness("arav-gemini") == "gemini"
    assert reflexd.detect_harness("arav-cursor") == "cursor"
    # Default: unknown suffix → claude (most common host).
    assert reflexd.detect_harness("alice") == "claude"


# ---------------------------------------------------------------------------
# Headless adapter — mocks subprocess + claude_agent_sdk
# ---------------------------------------------------------------------------


def test_headless_adapter_routes_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each harness routes to a different subprocess argv shape.

    All four harnesses (claude, codex, gemini, cursor) shell out now —
    claude moved from claude_agent_sdk.query to ``claude --print``. This
    is the smoke test that proves the dispatch table still routes
    correctly per name.
    """
    adapter = reflexd.HeadlessAdapter(timeout_s=5)

    class FakeProc:
        def __init__(self, stdout: bytes) -> None:
            self._stdout = stdout
            self.returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout, b""

        def kill(self) -> None:  # pragma: no cover
            pass

        async def wait(self) -> int:  # pragma: no cover
            return 0

    # Pretend every harness binary is on PATH so the pre-flight check passes.
    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: f"/fake/{_b}")

    async def dispatch(*argv: str, **_k: Any) -> FakeProc:
        if argv[0] == "claude":
            return FakeProc(b"hello from claude\n")
        if argv[0] == "codex":
            ndjson = (
                json.dumps({"delta": "hi "}) + "\n"
                + json.dumps({"delta": "codex"}) + "\n"
            ).encode()
            return FakeProc(ndjson)
        if argv[0] == "gemini":
            return FakeProc(b"hi gemini")
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", dispatch)

    async def go() -> None:
        out_claude = await adapter.run("claude", context="ctx")
        assert "hello from claude" in out_claude

        out_codex = await adapter.run("codex", context="ctx")
        assert "hi codex" in out_codex

        out_gemini = await adapter.run("gemini", context="ctx")
        assert "hi gemini" in out_gemini

    asyncio.run(go())


def test_cursor_falls_back_to_inbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When cursor-agent binary is missing, write to ~/.cursor/inbox/."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    async def missing_exec(*argv: str, **k: Any) -> Any:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing_exec)

    adapter = reflexd.HeadlessAdapter(timeout_s=2)
    out = asyncio.run(adapter.run("cursor", context="please reply"))
    # Adapter returns either fallback message or the "binary missing" sentinel
    # depending on which guard fires first; both are acceptable outcomes.
    assert "cursor-agent" in out or "wrote prompt to" in out
    inbox = tmp_path / ".cursor" / "inbox"
    if "wrote prompt to" in out:
        assert inbox.exists() and any(inbox.iterdir())


# ---------------------------------------------------------------------------
# E2E smoke — mock relay via httpx.MockTransport
# ---------------------------------------------------------------------------


class FakeRelay:
    """Tiny httpx.MockTransport that emulates the bid/claim/post lane.

    Records every call so tests can assert the daemon hit the right path.
    """

    def __init__(self, *, sse_payload: bytes) -> None:
        self.sse_payload = sse_payload
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
                200,
                content=self.sse_payload,
                headers={"content-type": "text/event-stream"},
            )
        if path == "/v1/bid":
            body = json.loads(request.content)
            self.bid_calls.append(body)
            return httpx.Response(
                200,
                json={
                    "accepted": True,
                    "leader": body["participant"],
                    "leader_bid": body["bid"],
                    "window_expires_at": "2026-05-02T12:00:05+00:00",
                    "fairness_credit": 0.0,
                },
            )
        if path == "/v1/claim":
            body = json.loads(request.content)
            self.claim_calls.append(body)
            return httpx.Response(
                200,
                json={
                    "claimed": True,
                    "winner": SELF,
                    "bid": 1.0,
                    "claim_token": "tok",
                    "expires_at": "2026-05-02T12:00:10+00:00",
                    "candidates": [SELF],
                    "fairness_credit": {SELF: -1.0},
                },
            )
        if path.endswith("/messages"):
            body = json.loads(request.content)
            self.posts.append({"path": path, **body})
            return httpx.Response(200, json={"id": "msg-out", "status": "ok"})
        if "/history" in path:
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(404, json={"detail": f"unexpected path {path}"})


def _sse_message_event(envelope: dict[str, Any]) -> bytes:
    """Encode a single SSE ``event: message`` block."""
    return (
        f"event: connected\ndata: {{\"participant\":\"{SELF}\"}}\n\n"
        f"event: message\ndata: {json.dumps(envelope)}\n\n"
    ).encode()


def test_smoke_e2e_at_mention_triggers_bid_and_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: mocked SSE delivers @-mention → daemon bids, claims, posts."""
    envelope = {
        "id": "msg-1",
        "from_name": "arav",
        "to": SELF,
        "room": "quorus-may4-sprint",
        "content": f"@{SELF} can you check this?",
        "message_type": "chat",
        "timestamp": "2026-05-02T12:00:00+00:00",
    }
    fake = FakeRelay(sse_payload=_sse_message_event(envelope))

    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="mct_test_secret",
        participant_name=SELF,
        runtime_dir=tmp_path / "runtime",
        log_path=tmp_path / "reflexd.log",
        spawn_enabled=True,
    )
    cfg.runtime_dir.mkdir()

    # Adapter that returns a deterministic reply without spawning anything real.
    class StubAdapter:
        async def run(self, harness: str, *, context: str) -> str:
            assert harness == "claude"
            assert "Wake Intent" in context
            return "ack: I see your @-mention"

    daemon = reflexd.Reflexd(cfg, adapter=StubAdapter())  # type: ignore[arg-type]

    async def go() -> None:
        async with reflexd.RelayClient(
            relay_url=cfg.relay_url, api_key=cfg.api_key
        ) as relay:
            # Replace the AsyncClient with one wired to the mock transport.
            await relay._client.aclose()  # type: ignore[union-attr]
            relay._client = httpx.AsyncClient(
                base_url=cfg.relay_url,
                transport=fake.transport(),
                follow_redirects=True,
            )
            # Shorten the bid window so the test runs in <1s.
            monkeypatch.setattr(reflexd, "BID_WINDOW_SECONDS", 0.5)
            handled = await daemon.handle_room_message(relay, envelope)
            assert handled is True

    asyncio.run(go())

    # Assertions on the mocked lane:
    assert len(fake.bid_calls) == 1, f"expected exactly one /v1/bid POST, got {fake.bid_calls}"
    bid = fake.bid_calls[0]
    assert bid["participant"] == SELF
    assert bid["bid"] == pytest.approx(1.0)
    assert bid["room_id"] == "quorus-may4-sprint"

    assert len(fake.claim_calls) >= 1
    assert fake.claim_calls[0]["message_id"] == "msg-1"

    assert len(fake.posts) == 1, f"expected exactly one reply post, got {fake.posts}"
    post = fake.posts[0]
    assert post["from_name"] == SELF
    assert "ack:" in post["content"]


def test_smoke_e2e_self_message_skips_bid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the SSE event is from self, daemon must NOT bid (would loop)."""
    envelope = {
        "id": "msg-loop",
        "from_name": SELF,
        "to": "anyone",
        "room": "r",
        "content": "I am thinking about this?",
        "message_type": "chat",
        "timestamp": "2026-05-02T12:00:00+00:00",
    }
    fake = FakeRelay(sse_payload=_sse_message_event(envelope))
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="mct_test_secret",
        participant_name=SELF,
        runtime_dir=tmp_path / "runtime",
        log_path=tmp_path / "reflexd.log",
        spawn_enabled=False,
    )
    cfg.runtime_dir.mkdir()
    daemon = reflexd.Reflexd(cfg)

    async def go() -> None:
        async with reflexd.RelayClient(
            relay_url=cfg.relay_url, api_key=cfg.api_key
        ) as relay:
            await relay._client.aclose()  # type: ignore[union-attr]
            relay._client = httpx.AsyncClient(
                base_url=cfg.relay_url,
                transport=fake.transport(),
                follow_redirects=True,
            )
            handled = await daemon.handle_room_message(relay, envelope)
            assert handled is False

    asyncio.run(go())
    assert fake.bid_calls == []
    assert fake.posts == []


def test_busyfile_blocks_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When the TurnGuard busy-file exists, daemon queues the message and skips."""
    envelope = {
        "id": "msg-busy",
        "from_name": "arav",
        "to": SELF,
        "room": "r",
        "content": f"@{SELF} hey?",
        "message_type": "chat",
        "timestamp": "2026-05-02T12:00:00+00:00",
    }
    fake = FakeRelay(sse_payload=_sse_message_event(envelope))
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="mct_test_secret",
        participant_name=SELF,
        runtime_dir=tmp_path / "runtime",
        log_path=tmp_path / "reflexd.log",
        spawn_enabled=False,
    )
    cfg.runtime_dir.mkdir()
    # Drop a busy-file so TurnGuard fires. Use the helper so the e2e test
    # exercises the same code path the harnesses ship.
    from quorus.runtime import turnguard as _tg
    _tg.begin(SELF, tool="Bash", ttl=60, dir_override=cfg.runtime_dir)

    daemon = reflexd.Reflexd(cfg)

    async def go() -> None:
        async with reflexd.RelayClient(
            relay_url=cfg.relay_url, api_key=cfg.api_key
        ) as relay:
            await relay._client.aclose()  # type: ignore[union-attr]
            relay._client = httpx.AsyncClient(
                base_url=cfg.relay_url,
                transport=fake.transport(),
                follow_redirects=True,
            )
            handled = await daemon.handle_room_message(relay, envelope)
            assert handled is False

    asyncio.run(go())
    assert fake.bid_calls == []
    assert daemon._queue and daemon._queue[0]["id"] == "msg-busy"


def test_cli_help_runs() -> None:
    """``python scripts/reflexd.py --help`` must not crash."""
    parser = reflexd.build_argparser()
    # argparse exits via SystemExit on --help; smoke that the parser builds.
    ns = parser.parse_args(["status"])
    assert ns.command == "status"


def test_reflexd_queue_logs_warn_on_drop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L29 — when the bounded busy-queue overflows, the dropped envelope
    must be surfaced via ``logger.warning`` so operators can detect it.

    The previous behaviour silently popped the oldest envelope, which
    masked load problems where reflexd was busy for so long that real
    user mentions were dropped on the floor.
    """
    import logging as _logging

    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="mct_test_secret",
        participant_name=SELF,
        runtime_dir=tmp_path / "runtime",
        log_path=tmp_path / "reflexd.log",
        spawn_enabled=False,
    )
    cfg.runtime_dir.mkdir()

    # Force the busy-file branch on every call.
    from quorus.runtime import turnguard as _tg
    _tg.begin(SELF, tool="Bash", ttl=60, dir_override=cfg.runtime_dir)

    daemon = reflexd.Reflexd(cfg)
    fake = FakeRelay(sse_payload=b"")

    async def push_n(n: int) -> None:
        async with reflexd.RelayClient(
            relay_url=cfg.relay_url, api_key=cfg.api_key
        ) as relay:
            await relay._client.aclose()  # type: ignore[union-attr]
            relay._client = httpx.AsyncClient(
                base_url=cfg.relay_url,
                transport=fake.transport(),
                follow_redirects=True,
            )
            for i in range(n):
                env = {
                    "id": f"msg-{i:03d}",
                    "from_name": "arav",
                    "to": SELF,
                    "room": "r",
                    "content": f"@{SELF} #{i}?",
                    "message_type": "chat",
                    "timestamp": "2026-05-02T12:00:00+00:00",
                }
                await daemon.handle_room_message(relay, env)

    # Cap is 32 — push 35 to overflow by 3 and ensure 3 warning lines.
    caplog.set_level(_logging.WARNING, logger="reflexd")
    asyncio.run(push_n(35))

    assert len(daemon._queue) == 32
    drop_warnings = [
        rec for rec in caplog.records
        if rec.levelno == _logging.WARNING
        and "queue cap (32) exceeded" in rec.getMessage()
    ]
    assert len(drop_warnings) == 3, (
        f"expected 3 drop warnings, got {len(drop_warnings)}: "
        f"{[r.getMessage() for r in drop_warnings]}"
    )
    # FIFO eviction: msg-000, msg-001, msg-002 are gone; survivors run
    # from msg-003 to msg-034 (32 entries).
    assert daemon._queue[0]["id"] == "msg-003"
    assert daemon._queue[-1]["id"] == "msg-034"


# ---------------------------------------------------------------------------
# Participant-name safety check (refuse non-agent suffix)
# ---------------------------------------------------------------------------


def _mk_cfg(name: str, *, allow_non_agent: bool = False) -> "reflexd.ReflexdConfig":
    """Minimal in-memory ReflexdConfig for participant-validation tests."""
    return reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="k",
        participant_name=name,
        spawn_enabled=False,
        allow_non_agent=allow_non_agent,
    )


def test_reflexd_refuses_human_participant_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Daemon must refuse to construct under a non-agent participant name.

    Without this guard, ``triage_local`` would IGNORE every chat from "arav"
    as a self-message and the daemon would never wake — silent failure.
    """
    cfg = _mk_cfg("arav")
    with pytest.raises(SystemExit) as exc_info:
        reflexd.Reflexd(cfg)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "refusing to start" in err
    assert "'arav'" in err
    assert "no agent suffix" in err


def test_reflexd_accepts_claude_suffix() -> None:
    cfg = _mk_cfg("arav-claude")
    daemon = reflexd.Reflexd(cfg)
    assert daemon.config.participant_name == "arav-claude"


def test_reflexd_accepts_claude_1m_suffix() -> None:
    cfg = _mk_cfg("arav-claude-1m")
    daemon = reflexd.Reflexd(cfg)
    assert daemon.config.participant_name == "arav-claude-1m"


def test_reflexd_accepts_claude_2_suffix() -> None:
    cfg = _mk_cfg("arav-claude-2")
    daemon = reflexd.Reflexd(cfg)
    assert daemon.config.participant_name == "arav-claude-2"


def test_reflexd_accepts_claude_desktop_suffix() -> None:
    cfg = _mk_cfg("arav-claude-desktop")
    daemon = reflexd.Reflexd(cfg)
    assert daemon.config.participant_name == "arav-claude-desktop"


def test_reflexd_accepts_codex_suffix() -> None:
    cfg = _mk_cfg("arav-codex")
    daemon = reflexd.Reflexd(cfg)
    assert daemon.config.participant_name == "arav-codex"


def test_reflexd_accepts_gemini_suffix() -> None:
    cfg = _mk_cfg("arav-gemini")
    daemon = reflexd.Reflexd(cfg)
    assert daemon.config.participant_name == "arav-gemini"


def test_reflexd_accepts_cursor_suffix() -> None:
    cfg = _mk_cfg("arav-cursor")
    daemon = reflexd.Reflexd(cfg)
    assert daemon.config.participant_name == "arav-cursor"


def test_reflexd_allow_non_agent_participant_override() -> None:
    """``--allow-non-agent-participant`` (allow_non_agent=True) bypasses check."""
    cfg = _mk_cfg("arav", allow_non_agent=True)
    daemon = reflexd.Reflexd(cfg)  # must not raise
    assert daemon.config.participant_name == "arav"
    assert daemon.config.allow_non_agent is True


def test_reflexd_refuses_empty_participant() -> None:
    """Empty / whitespace participants must also fail closed."""
    with pytest.raises(SystemExit) as exc_info:
        reflexd.Reflexd(_mk_cfg(""))
    assert exc_info.value.code == 2


def test_reflexd_refuses_substring_match_without_suffix() -> None:
    """``claude-arav`` (claude as prefix) is NOT a valid agent name.

    Only end-anchored suffixes are accepted — guards against confused
    routing where ``detect_harness`` substring-matches but the triage
    self-message check uses literal equality on full name.
    """
    cfg = _mk_cfg("claude-arav")
    with pytest.raises(SystemExit) as exc_info:
        reflexd.Reflexd(cfg)
    assert exc_info.value.code == 2


def test_is_agent_participant_helper() -> None:
    """The exposed helper agrees with the constructor check."""
    assert reflexd.is_agent_participant("arav-claude") is True
    assert reflexd.is_agent_participant("arav-claude-1m") is True
    assert reflexd.is_agent_participant("arav-codex") is True
    assert reflexd.is_agent_participant("arav-gemini") is True
    assert reflexd.is_agent_participant("arav-cursor") is True
    assert reflexd.is_agent_participant("ARAV-CLAUDE") is True  # case-insensitive
    assert reflexd.is_agent_participant("arav") is False
    assert reflexd.is_agent_participant("") is False
    assert reflexd.is_agent_participant("claude-arav") is False  # prefix only


def test_cli_parses_allow_non_agent_flag() -> None:
    """argparse exposes ``--allow-non-agent-participant`` on start."""
    parser = reflexd.build_argparser()
    ns = parser.parse_args(["start", "--allow-non-agent-participant"])
    assert ns.allow_non_agent_participant is True
    ns2 = parser.parse_args(["start"])
    assert ns2.allow_non_agent_participant is False


def test_iter_sse_events_parses_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover the SSE parser path independently of the daemon."""
    payload = (
        b"event: connected\ndata: {\"x\":1}\n\n"
        b": keepalive\n"
        b"event: message\ndata: {\"a\":2}\n\n"
    )

    handler = httpx.MockTransport(
        lambda req: httpx.Response(
            200, content=payload, headers={"content-type": "text/event-stream"}
        )
    )
    async def go() -> list[Any]:
        events: list[Any] = []
        async with httpx.AsyncClient(transport=handler, base_url="http://t") as client:
            async for ev, data in reflexd.iter_sse_events(client, "http://t/stream"):
                events.append((ev, data))
                if len(events) >= 2:
                    break
        return events

    out = asyncio.run(go())
    assert ("connected", {"x": 1}) in out
    assert ("message", {"a": 2}) in out
