"""Real-claude reflexd path tests — never make real CLI calls in CI.

Covers the non-stub claude adapter path, the QOD voice section, the
reply-depth anti-loop guard, the startup warning when the claude binary
is missing, and the ``quorus reflexd doctor`` checklist.

The claude adapter shells out to ``claude --print`` (Claude Code CLI in
headless mode) instead of the old ``claude_agent_sdk`` Python import.
That swap is what gives Quorus a "no new API key" MVP — Claude Code's
own OAuth/keychain handles auth. ``ANTHROPIC_API_KEY`` is no longer
consulted by reflexd at all.

Every subprocess spawn is mocked so these tests never reach the real
binary. The contract here is "the daemon called the CLI with the
expected argv and the wire shape is preserved" — not "Claude Code is
reachable".
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

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


@pytest.fixture(autouse=True)
def _isolate_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clean env + module-level fallback flag between tests.

    ``log_anthropic_api_key_status`` toggles
    :data:`reflexd._AUTO_FALLBACK_TO_STUB` (an in-process module attr)
    when neither the API key nor the stub flag is present. Without an
    explicit reset, that flag stays True across every later test.
    """
    for key in (
        "ANTHROPIC_API_KEY",
        "REFLEXD_STUB_REPLY",
        "REFLEXD_API_KEY",
        "API_KEY",
        "REFLEXD_PARTICIPANT",
        "QUORUS_PARTICIPANT",
        "REFLEXD_RELAY_URL",
        "RELAY_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    # Reset BEFORE each test (a previous test may have triggered fallback)
    # and AFTER each test (yield) so the flag never leaks into adjacent
    # test files like tests/test_reflexd_adapters.py.
    reflexd.reset_auto_fallback()
    yield
    reflexd.reset_auto_fallback()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProc:
    """Async-subprocess test double matching ``asyncio.subprocess.Process``.

    Mirrors the helper in test_reflexd_adapters.py — kept local so this
    file stays self-contained.
    """

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:  # pragma: no cover — no timeouts in this file
        pass

    async def wait(self) -> int:  # pragma: no cover
        return self.returncode


def _install_fake_claude_cli(
    monkeypatch: pytest.MonkeyPatch,
    *,
    captured: dict[str, Any] | None = None,
    reply_text: str = "ok",
) -> dict[str, Any]:
    """Pretend the ``claude`` CLI is on PATH and capture the argv it's invoked with.

    Returns the ``captured`` dict so the caller can assert on the argv
    (and therefore the prompt) the adapter passes to subprocess.
    Pass a pre-existing dict to share state between calls.
    """
    captured = captured if captured is not None else {}

    async def fake_exec(*argv: str, **_k: Any) -> _FakeProc:
        captured["argv"] = list(argv)
        captured["prompt"] = argv[-1] if argv else ""
        return _FakeProc(stdout=reply_text.encode() + b"\n")

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/claude")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


def _strip_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop env vars that influence the real-vs-stub branch decision.

    ``ANTHROPIC_API_KEY`` is no longer consulted by reflexd, but we still
    strip it to keep tests deterministic across environments where it
    happens to be set.
    """
    for key in (
        "ANTHROPIC_API_KEY",
        "REFLEXD_STUB_REPLY",
        "REFLEXD_API_KEY",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _stub_claude_version_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str | None,
) -> None:
    """Pin ``probe_harness_version("claude")`` to a deterministic answer.

    Used by the startup-warning tests so we don't depend on whether the
    real Claude Code CLI happens to be installed on the test host.
    """
    real_probe = reflexd.probe_harness_version

    def fake_probe(harness: str, *, timeout_s: float = 3.0) -> str | None:
        if harness == "claude":
            return version
        return real_probe(harness, timeout_s=timeout_s)

    monkeypatch.setattr(reflexd, "probe_harness_version", fake_probe)


class _OkResp:
    status_code = 200


class _OkClient:
    """httpx.Client stub that always returns 200 — used by doctor tests."""

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    def __enter__(self) -> "_OkClient":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def get(self, _url: str) -> _OkResp:
        return _OkResp()


def _reload_cli_for_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Reroute Path.home and reload cli modules; return the cli module."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import importlib

    import quorus.config as _qcfg
    importlib.reload(_qcfg)
    import quorus_cli.cli as _cli
    importlib.reload(_cli)
    return _cli


# ---------------------------------------------------------------------------
# Startup-warning tests — log_claude_adapter_status()
# ---------------------------------------------------------------------------
# After the SDK→CLI swap the fallback decision is keyed off "is the claude
# binary on PATH" instead of "is ANTHROPIC_API_KEY set". The old name
# log_anthropic_api_key_status is preserved as a back-compat alias.


def test_reflexd_logs_warning_when_claude_cli_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """No claude CLI + no stub flag → WARNING logged, returns 'fallback-stub'."""
    _strip_claude_env(monkeypatch)
    _stub_claude_version_probe(monkeypatch, version=None)
    caplog.set_level(logging.WARNING, logger="reflexd")
    state = reflexd.log_claude_adapter_status()
    assert state == "fallback-stub"
    assert any(
        "claude CLI not found" in rec.message
        and "REFLEXD_STUB_REPLY" in rec.message
        for rec in caplog.records
    ), f"Expected fallback warning in log; got {[r.message for r in caplog.records]}"
    # Auto-fallback toggles the module-level flag so the adapter takes
    # the stub path WITHOUT mutating os.environ — env mutations leak
    # across pytest test files.
    assert reflexd.is_stub_mode() is True
    assert __import__("os").environ.get("REFLEXD_STUB_REPLY") is None


def test_reflexd_falls_back_to_stub_when_no_claude_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude CLI missing + auto-fallback set → adapter.run returns the stub."""
    _strip_claude_env(monkeypatch)
    _stub_claude_version_probe(monkeypatch, version=None)
    reflexd.log_claude_adapter_status()  # triggers the auto-fallback
    adapter = reflexd.HeadlessAdapter(timeout_s=2)
    out = asyncio.run(
        adapter.run("claude", context="@me what is the stack?")
    )
    assert out.startswith("(reflexd-stub)"), out
    # No exception, no subprocess spawn, just a stub reply.


def test_reflexd_uses_claude_cli_when_present(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Claude CLI on PATH → INFO log names the version, adapter spawns subprocess.

    No ANTHROPIC_API_KEY is required — that's the whole point of the
    SDK→CLI swap. The Claude Code CLI's own OAuth handles auth.
    """
    _strip_claude_env(monkeypatch)
    _stub_claude_version_probe(monkeypatch, version="2.1.126 (Claude Code)")
    captured = _install_fake_claude_cli(monkeypatch, reply_text="hello team")
    caplog.set_level(logging.INFO, logger="reflexd")
    state = reflexd.log_claude_adapter_status()
    assert state == "real"
    # Log line shape matches the contract.
    assert any(
        "real-claude adapter active" in rec.message
        and "claude CLI 2.1.126" in rec.message
        for rec in caplog.records
    )

    adapter = reflexd.HeadlessAdapter(timeout_s=2)
    out = asyncio.run(adapter.run("claude", context="hi-claude"))
    assert out == "hello team"
    # Argv pinned shape after CRIT-7: ``--`` separates options from prompt
    # so a leading-dash chat body is not parsed as a flag.
    assert captured["argv"] == ["claude", "--print", "--", "hi-claude"]


def test_reflexd_explicit_stub_reports_stub_state(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """REFLEXD_STUB_REPLY=1 → state='stub' regardless of CLI presence."""
    _strip_claude_env(monkeypatch)
    monkeypatch.setenv("REFLEXD_STUB_REPLY", "1")
    caplog.set_level(logging.INFO, logger="reflexd")
    state = reflexd.log_claude_adapter_status()
    assert state == "stub"
    # No fallback warning when explicit stub mode.
    assert not any(
        "claude CLI not found" in rec.message
        for rec in caplog.records
    )


def test_log_anthropic_api_key_status_is_back_compat_alias() -> None:
    """The old name still resolves to the new implementation.

    External callers (older docs, support scripts) can keep the import
    path working through one release while we update them in lock step.
    """
    assert reflexd.log_anthropic_api_key_status is reflexd.log_claude_adapter_status


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
# Subprocess argv — verbatim wire-format regression
# ---------------------------------------------------------------------------
# Replaces the old TextBlock/ToolUseBlock filtering test. The Claude Code
# CLI in --print mode emits plain text on stdout, so the adapter no longer
# needs to skip non-text content blocks. What we still pin is the argv
# shape — every reflexd→claude call MUST go through ``claude --print
# <context>`` with the full QOD prompt as a single argv element. If
# anything in the build path stringifies the context wrong (e.g. coerces
# to repr) this test fails.


def test_claude_subprocess_receives_verbatim_qod_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact prompt the daemon builds must reach claude --print verbatim."""
    captured: dict[str, Any] = {}

    async def fake_exec(*argv: str, **_k: Any) -> _FakeProc:
        captured["argv"] = list(argv)
        return _FakeProc(stdout=b"Looking now.\n")

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/claude")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="k",
        participant_name=SELF, spawn_enabled=False,
    )
    daemon = reflexd.Reflexd(cfg)
    prompt = daemon._build_prompt(
        {"id": "x", "from_name": "u", "room": "r", "content": "ping"},
        history=[],
    )

    adapter = reflexd.HeadlessAdapter(timeout_s=2)
    out = asyncio.run(adapter.run("claude", context=prompt))
    assert out == "Looking now."
    # Pinned argv shape — flag rename / stringification regression catcher.
    # After CRIT-7 the prompt sits at argv[3], past the ``--`` separator.
    assert captured["argv"][:3] == ["claude", "--print", "--"]
    assert captured["argv"][3] == prompt
    assert len(captured["argv"]) == 4


# ---------------------------------------------------------------------------
# CLI: quorus reflexd doctor
# ---------------------------------------------------------------------------


def _stub_doctor_claude_cli(
    monkeypatch: pytest.MonkeyPatch, _cli: Any, *, present: bool,
) -> None:
    """Pin ``_reflexd_doctor_probe_claude_cli`` for the doctor tests.

    The doctor probe shells out to ``claude --version`` and ``claude
    --help`` — both calls would actually run the real binary on the test
    host. Pin the function so tests don't depend on what's installed.
    """
    if present:
        monkeypatch.setattr(
            _cli, "_reflexd_doctor_probe_claude_cli",
            lambda: (True, "v2.1.126 (Claude Code) (Claude Code OAuth handles auth)"),
        )
    else:
        monkeypatch.setattr(
            _cli, "_reflexd_doctor_probe_claude_cli",
            lambda: (False, "claude CLI not on PATH — install from https://claude.ai/code"),
        )


def test_doctor_prints_green_check_when_all_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """All probes green → 'all checks passed', exit 0.

    The claude CLI row replaces the old claude_agent_sdk + ANTHROPIC_API_KEY
    rows. No new API key needed — Claude Code OAuth handles auth.
    """
    _cli = _reload_cli_for_doctor(tmp_path, monkeypatch)
    _stub_doctor_claude_cli(monkeypatch, _cli, present=True)
    monkeypatch.setenv("REFLEXD_API_KEY", "rfx-test")
    monkeypatch.setenv("REFLEXD_PARTICIPANT", "arav-claude")
    monkeypatch.setenv("RELAY_URL", "http://localhost:65535")

    runtime_dir = tmp_path / ".quorus" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "reflexd.arav-claude.pid").write_text("99999", encoding="utf-8")
    monkeypatch.setattr(_cli, "_pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(_cli.httpx, "Client", _OkClient)

    args = argparse.Namespace(participant="arav-claude")
    with pytest.raises(SystemExit) as exc:
        _cli._reflexd_doctor(args)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    # New row label is "claude CLI", not "claude_agent_sdk".
    assert "claude CLI" in out
    # And the doctor MUST NOT print an ANTHROPIC_API_KEY check anymore —
    # that's the whole "no new keys" guarantee.
    assert "ANTHROPIC_API_KEY" not in out
    assert "all checks passed" in out
    assert "✓" in out
    assert "✗" not in out


def test_doctor_prints_red_check_when_claude_cli_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Missing claude CLI → red ✗ on that line, exit 1."""
    _cli = _reload_cli_for_doctor(tmp_path, monkeypatch)
    _stub_doctor_claude_cli(monkeypatch, _cli, present=False)
    monkeypatch.setenv("REFLEXD_API_KEY", "rfx-test")
    monkeypatch.setenv("REFLEXD_PARTICIPANT", "arav-claude")
    monkeypatch.setenv("RELAY_URL", "http://localhost:65535")
    monkeypatch.setattr(_cli.httpx, "Client", _OkClient)

    args = argparse.Namespace(participant="arav-claude")
    with pytest.raises(SystemExit) as exc:
        _cli._reflexd_doctor(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "claude CLI" in out
    assert "✗" in out
    assert "not on path" in out.lower()
