"""Real-claude reflexd path tests — never make real API calls in CI.

Covers the non-stub claude adapter path, the QOD voice section, the
reply-depth anti-loop guard, the startup warning when ANTHROPIC_API_KEY
is missing, and the ``quorus reflexd doctor`` checklist.

Every claude_agent_sdk import is replaced with a fake module so these
tests never reach the real SDK or the Anthropic API. The contract here
is "the daemon called the SDK with the expected args and the wire shape
is preserved" — not "Anthropic is reachable".
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# scripts/ is not a package — load reflexd by path. Mirrors the loader
# the other reflexd test modules use; the module is cached in sys.modules
# under the name "reflexd" so patches reach it.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFLEXD_PATH = _REPO_ROOT / "scripts" / "reflexd.py"
_spec = importlib.util.spec_from_file_location("reflexd", _REFLEXD_PATH)
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


SELF = "arav-claude"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_fake_claude_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    captured: dict[str, Any] | None = None,
    reply_text: str = "ok",
) -> dict[str, Any]:
    """Replace ``claude_agent_sdk`` in sys.modules with a controllable fake.

    Returns the ``captured`` dict so the caller can assert on the prompt
    and options passed to ``query``. Pass a pre-existing dict to share
    state between calls.
    """
    captured = captured if captured is not None else {}

    async def fake_query(*, prompt: str, options: Any = None) -> Any:
        captured["prompt"] = prompt
        captured["options"] = options

        class _Block:
            text = reply_text

        class _Msg:
            content = [_Block()]

        yield _Msg()

    fake_module = type(sys)("claude_agent_sdk")
    fake_module.query = fake_query
    fake_module.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)
    return captured


def _strip_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop env vars that influence the real-vs-stub branch decision."""
    for key in (
        "ANTHROPIC_API_KEY",
        "REFLEXD_STUB_REPLY",
        "REFLEXD_API_KEY",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Startup-warning tests — log_anthropic_api_key_status()
# ---------------------------------------------------------------------------


def test_reflexd_logs_warning_when_no_anthropic_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """No key + no stub flag → WARNING logged, returns 'fallback-stub'."""
    _strip_claude_env(monkeypatch)
    caplog.set_level(logging.WARNING, logger="reflexd")
    state = reflexd.log_anthropic_api_key_status()
    assert state == "fallback-stub"
    assert any(
        "ANTHROPIC_API_KEY not set" in rec.message
        and "REFLEXD_STUB_REPLY" in rec.message
        for rec in caplog.records
    ), f"Expected fallback warning in log; got {[r.message for r in caplog.records]}"
    # Auto-fallback should set the env var so the adapter takes the stub path.
    assert __import__("os").environ.get("REFLEXD_STUB_REPLY") == "1"


def test_reflexd_falls_back_to_stub_when_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key + auto-fallback set → adapter.run returns the stub sentinel."""
    _strip_claude_env(monkeypatch)
    reflexd.log_anthropic_api_key_status()  # triggers the auto-fallback
    adapter = reflexd.HeadlessAdapter(timeout_s=2)
    out = asyncio.run(
        adapter.run("claude", context="@me what is the stack?")
    )
    assert out.startswith("(reflexd-stub)"), out
    # No exception, no API call, just a stub reply.


def test_reflexd_uses_claude_sdk_when_key_present(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Key present → INFO log includes sdk version, adapter calls fake query."""
    _strip_claude_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake")
    captured = _install_fake_claude_sdk(monkeypatch, reply_text="hello team")
    caplog.set_level(logging.INFO, logger="reflexd")
    state = reflexd.log_anthropic_api_key_status()
    assert state == "real"
    # Log line shape matches the contract.
    assert any(
        "real-claude adapter active" in rec.message
        for rec in caplog.records
    )

    adapter = reflexd.HeadlessAdapter(timeout_s=2)
    out = asyncio.run(adapter.run("claude", context="hi-claude"))
    assert out == "hello team"
    assert captured["prompt"] == "hi-claude"
    assert captured["options"] == {
        "permission_mode": "bypassPermissions",
        "max_turns": 1,
    }


def test_reflexd_explicit_stub_reports_stub_state(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """REFLEXD_STUB_REPLY=1 + no key → state='stub', no warning."""
    _strip_claude_env(monkeypatch)
    monkeypatch.setenv("REFLEXD_STUB_REPLY", "1")
    caplog.set_level(logging.INFO, logger="reflexd")
    state = reflexd.log_anthropic_api_key_status()
    assert state == "stub"
    # No fallback warning when explicit stub mode.
    assert not any(
        "ANTHROPIC_API_KEY not set" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# QOD voice section tests
# ---------------------------------------------------------------------------


def test_qod_voice_section_in_render() -> None:
    """The voice section must appear in render_qod() and the agent-loop render.

    The bullets are a fixed list — every one must appear verbatim, plus the
    'senior engineer' header.
    """
    from quorus.operating_discipline import (
        QOD_VOICE_BULLETS,
        QOD_VOICE_HEADER,
        render_qod,
        render_qod_for_agent_loop,
        render_qod_voice,
    )

    voice = render_qod_voice()
    for marker in (
        "senior engineer on a team",
        "Acknowledge before working",
        "Push back when wrong",
        "Cite specifics",
        "Brevity > thoroughness",
        "Never say \"as an AI\"",
        "When you finish",
    ):
        assert marker in voice, f"voice block missing marker: {marker!r}"

    # The same voice text must be embedded in render_qod() and the agent-loop
    # render — those are the two surfaces reflexd's wake context concatenates.
    assert QOD_VOICE_HEADER in render_qod()
    assert QOD_VOICE_HEADER in render_qod_for_agent_loop()
    for bullet in QOD_VOICE_BULLETS:
        assert bullet in render_qod()
        assert bullet in render_qod_for_agent_loop()


def test_qod_voice_appears_in_reflexd_wake_prompt() -> None:
    """The voice section must travel through reflexd's _build_prompt verbatim."""
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="k",
        participant_name=SELF,
        spawn_enabled=False,
    )
    daemon = reflexd.Reflexd(cfg)
    prompt = daemon._build_prompt(
        {"id": "x", "from_name": "u", "room": "r", "content": "hi?"},
        history=[],
    )
    assert "senior engineer on a team" in prompt
    assert "Push back when wrong" in prompt
    assert "Never say \"as an AI\"" in prompt


# ---------------------------------------------------------------------------
# Reply-depth anti-loop guard
# ---------------------------------------------------------------------------


def test_envelope_reply_depth_defaults_to_zero() -> None:
    assert reflexd.envelope_reply_depth({}) == 0
    assert reflexd.envelope_reply_depth({"_reply_depth": None}) == 0
    assert reflexd.envelope_reply_depth({"_reply_depth": "not-an-int"}) == 0
    # Negatives are clamped to 0 — defensive against malformed upstream data.
    assert reflexd.envelope_reply_depth({"_reply_depth": -3}) == 0


def test_envelope_reply_depth_reads_top_level_and_metadata() -> None:
    assert reflexd.envelope_reply_depth({"_reply_depth": 2}) == 2
    assert reflexd.envelope_reply_depth(
        {"metadata": {"reply_depth": 4}}
    ) == 4
    # Top-level wins over metadata when both are present.
    assert reflexd.envelope_reply_depth(
        {"_reply_depth": 1, "metadata": {"reply_depth": 9}}
    ) == 1


def test_should_break_reply_chain_threshold() -> None:
    """Chain depth > MAX (=3) means we are about to post a 4th reply."""
    assert not reflexd.should_break_reply_chain({"_reply_depth": 0})
    assert not reflexd.should_break_reply_chain({"_reply_depth": 1})
    assert not reflexd.should_break_reply_chain({"_reply_depth": 2})
    # Parent at depth 3 ⇒ our reply would be depth 4, which is > MAX.
    assert reflexd.should_break_reply_chain({"_reply_depth": 3})
    assert reflexd.should_break_reply_chain({"_reply_depth": 99})


def test_reply_depth_counter_breaks_at_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handle_room_message refuses to bid when depth would exceed 3.

    We mock RelayClient.submit_bid so a real bid would crash the test; if
    the guard works, submit_bid is never called.
    """
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="k",
        participant_name=SELF,
        spawn_enabled=False,
    )
    daemon = reflexd.Reflexd(cfg)

    class _RelayProbe:
        def __init__(self) -> None:
            self.bid_calls = 0

        async def submit_bid(self, **_kw: Any) -> None:  # pragma: no cover
            self.bid_calls += 1
            raise AssertionError("submit_bid called — chain guard failed")

        async def fetch_recent(self, **_kw: Any) -> list[Any]:
            return []

        async def claim(self, **_kw: Any) -> dict[str, Any]:  # pragma: no cover
            return {"claimed": False}

        async def post_reply(self, **_kw: Any) -> dict[str, Any]:  # pragma: no cover
            raise AssertionError("post_reply called — chain guard failed")

    relay = _RelayProbe()
    envelope = {
        "id": "msg-deep",
        "from_name": "arav",
        "room": "demo",
        "content": "@arav-claude please?",
        "message_type": "chat",
        "_reply_depth": 3,
    }
    out = asyncio.run(daemon.handle_room_message(relay, envelope))  # type: ignore[arg-type]
    assert out is False
    assert relay.bid_calls == 0


def test_reply_depth_counter_passes_when_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At depth 0 the chain guard does NOT short-circuit (sanity check).

    We stop the test before bid POST by failing triage: the message has no
    @-mention and no trailing ``?``, so triage returns IGNORE and the
    handler returns False BEFORE the chain check. Instead we directly call
    ``should_break_reply_chain`` here — the integration path is exercised
    in the prior test.
    """
    assert (
        reflexd.should_break_reply_chain({"_reply_depth": 0}) is False
    )


def test_reply_depth_appears_in_wake_context() -> None:
    """The wake prompt must include the depth so the harness sees it."""
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="k",
        participant_name=SELF,
        spawn_enabled=False,
    )
    daemon = reflexd.Reflexd(cfg)
    envelope = {
        "id": "x", "from_name": "u", "room": "r",
        "content": "hi?", "_reply_depth": 2,
    }
    prompt = daemon._build_prompt(envelope, history=[])
    assert "Reply chain depth: 2" in prompt
    assert f"max {reflexd.MAX_REPLY_DEPTH}" in prompt


# ---------------------------------------------------------------------------
# AssistantMessage filtering — TextBlock-only extraction
# ---------------------------------------------------------------------------


def test_run_claude_extracts_only_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ToolUseBlock-style objects must NOT be concatenated into the reply."""

    captured: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any = None) -> Any:
        captured["prompt"] = prompt

        class _TextBlock:
            text = "Looking now."

        class _ToolBlock:
            # No `.text` attr — would be silently included if we just
            # walked .content.
            id = "tool-1"
            name = "Read"
            input = {"file_path": "/x"}

        class _Msg:
            content = [_ToolBlock(), _TextBlock(), _ToolBlock()]

        yield _Msg()

    fake_module = type(sys)("claude_agent_sdk")
    fake_module.query = fake_query
    fake_module.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    adapter = reflexd.HeadlessAdapter(timeout_s=2)
    out = asyncio.run(adapter.run("claude", context="ping"))
    assert out == "Looking now."


# ---------------------------------------------------------------------------
# CLI: quorus reflexd doctor
# ---------------------------------------------------------------------------


def test_doctor_prints_green_check_when_all_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """All probes green → 'all checks passed', exit 0."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Reload modules whose module-level constants captured Path.home() at
    # import time. Mirror the runtime_home fixture in test_cli_reflexd.py.
    import importlib
    import quorus.config as _qcfg
    importlib.reload(_qcfg)
    import quorus_cli.cli as _cli
    importlib.reload(_cli)

    # All-green env.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("REFLEXD_API_KEY", "rfx-test")
    monkeypatch.setenv("REFLEXD_PARTICIPANT", "arav-claude")
    monkeypatch.setenv("RELAY_URL", "http://localhost:65535")

    # Fake daemon pidfile + alive-check.
    runtime_dir = tmp_path / ".quorus" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pidfile = runtime_dir / "reflexd.arav-claude.pid"
    pidfile.write_text("99999", encoding="utf-8")
    monkeypatch.setattr(_cli, "_pid_is_alive", lambda _pid: True)

    # Stub relay /health.
    class _OkResp:
        status_code = 200

    class _OkClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def __enter__(self) -> "_OkClient":
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

        def get(self, _url: str) -> _OkResp:
            return _OkResp()

    monkeypatch.setattr(_cli.httpx, "Client", _OkClient)

    args = argparse.Namespace(participant="arav-claude")
    with pytest.raises(SystemExit) as exc:
        _cli._reflexd_doctor(args)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "claude_agent_sdk" in out
    assert "all checks passed" in out
    assert "✓" in out
    assert "✗" not in out


def test_doctor_prints_red_check_when_anthropic_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Missing ANTHROPIC_API_KEY → red ✗ on that line, exit 1."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import importlib
    import quorus.config as _qcfg
    importlib.reload(_qcfg)
    import quorus_cli.cli as _cli
    importlib.reload(_cli)

    # Strip the var explicitly.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Provide every other piece so this is the only failing probe (well,
    # plus the daemon-running one — that's expected for a fresh tmp dir).
    monkeypatch.setenv("REFLEXD_API_KEY", "rfx-test")
    monkeypatch.setenv("REFLEXD_PARTICIPANT", "arav-claude")
    monkeypatch.setenv("RELAY_URL", "http://localhost:65535")

    class _OkResp:
        status_code = 200

    class _OkClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def __enter__(self) -> "_OkClient":
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

        def get(self, _url: str) -> _OkResp:
            return _OkResp()

    monkeypatch.setattr(_cli.httpx, "Client", _OkClient)

    args = argparse.Namespace(participant="arav-claude")
    with pytest.raises(SystemExit) as exc:
        _cli._reflexd_doctor(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out
    assert "✗" in out
    assert "missing" in out.lower()
    # The summary line must mention the failing probe.
    assert "ANTHROPIC_API_KEY" in out
