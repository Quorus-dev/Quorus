"""Contract tests for the four headless harness adapters in ``scripts/reflexd.py``.

These tests pin the exact argv shape, timeout behaviour, error handling, and
QOD-injection contract for {claude, codex, gemini, cursor}. They run in <1s
total because every external call (subprocess.run, asyncio.create_subprocess_exec,
shutil.which, claude_agent_sdk.query) is mocked. If a vendor renames a flag or
the daemon stops injecting the QOD, the corresponding test here fails — which
is the point.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# scripts/ is not a package — mirror tests/test_reflexd.py's import shim.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFLEXD_PATH = _REPO_ROOT / "scripts" / "reflexd.py"
_spec = importlib.util.spec_from_file_location("reflexd", _REFLEXD_PATH)
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


SELF = "arav-claude"


# ---------------------------------------------------------------------------
# Argv-shape pinning helpers — purely synchronous, no IO
# ---------------------------------------------------------------------------


def test_build_codex_argv_pins_exact_shape() -> None:
    """Codex CLI contract: ``codex exec --json --prompt <ctx>``."""
    out = reflexd.build_codex_argv("hello world")
    assert out == ["codex", "exec", "--json", "--prompt", "hello world"]


def test_build_gemini_argv_pins_exact_shape() -> None:
    """Gemini CLI contract: ``gemini --prompt <ctx>``."""
    out = reflexd.build_gemini_argv("hello world")
    assert out == ["gemini", "--prompt", "hello world"]


def test_build_cursor_argv_pins_exact_shape() -> None:
    """Cursor CLI contract: ``cursor-agent --headless --prompt <ctx>``."""
    out = reflexd.build_cursor_argv("hello world")
    assert out == ["cursor-agent", "--headless", "--prompt", "hello world"]


# ---------------------------------------------------------------------------
# Shared helpers for adapter tests
# ---------------------------------------------------------------------------


class _FakeProc:
    """Async-subprocess test double matching ``asyncio.subprocess.Process``."""

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            # Block "forever" — the adapter's wait_for must time out.
            await asyncio.sleep(60)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _run_adapter(harness: str, *, context: str = "ctx", timeout_s: int = 5) -> str:
    adapter = reflexd.HeadlessAdapter(timeout_s=timeout_s)
    return asyncio.run(adapter.run(harness, context=context))


def _qod_prompt(self_name: str = SELF) -> str:
    """Build the same prompt the daemon would, including the QOD."""
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="k",
        participant_name=self_name,
        spawn_enabled=False,
    )
    daemon = reflexd.Reflexd(cfg)
    return daemon._build_prompt(
        {"id": "x", "from_name": "u", "room": "r", "content": "hi?"},
        history=[],
    )


# ---------------------------------------------------------------------------
# Claude adapter contract — uses claude_agent_sdk.query, not subprocess
# ---------------------------------------------------------------------------


def test_claude_adapter_invokes_correct_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude path goes through claude_agent_sdk.query — assert the call shape.

    Contract: ``claude_agent_sdk.query(prompt=<ctx>, options=ClaudeAgentOptions(...))``.
    """
    captured: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any = None) -> Any:
        captured["prompt"] = prompt
        captured["options"] = options

        class _Block:
            text = "ok"

        class _Msg:
            content = [_Block()]

        yield _Msg()

    fake_module = type(sys)("claude_agent_sdk")
    fake_module.query = fake_query
    fake_module.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    out = _run_adapter("claude", context="hello-claude")
    assert out == "ok"
    assert captured["prompt"] == "hello-claude"
    # Options must request bypassPermissions and a single turn.
    assert captured["options"] == {"permission_mode": "bypassPermissions", "max_turns": 1}


def test_claude_adapter_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hanging claude_agent_sdk.query is caught by the adapter's broad except."""
    async def hanging_query(*, prompt: str, options: Any = None) -> Any:
        raise asyncio.TimeoutError("simulated hang")
        yield  # pragma: no cover — make this an async-gen syntactically

    fake_module = type(sys)("claude_agent_sdk")
    fake_module.query = hanging_query
    fake_module.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    out = _run_adapter("claude", context="ctx")
    assert "claude harness errored" in out


def test_claude_adapter_handles_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude SDK throwing maps to a clean error sentinel, not a crash."""
    async def boom_query(*, prompt: str, options: Any = None) -> Any:
        raise RuntimeError("sdk exploded")
        yield  # pragma: no cover

    fake_module = type(sys)("claude_agent_sdk")
    fake_module.query = boom_query
    fake_module.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    out = _run_adapter("claude", context="ctx")
    assert out.startswith("[reflexd]")
    assert "errored" in out


def test_claude_adapter_handles_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """No claude_agent_sdk installed → return a clear sentinel, no exception."""
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    # And ensure import yields ImportError.
    if isinstance(__builtins__, dict):
        real_import = __builtins__["__import__"]
    else:
        real_import = __builtins__.__import__

    def bad_import(name: str, *a: Any, **k: Any) -> Any:
        if name == "claude_agent_sdk":
            raise ImportError("not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", bad_import)
    out = _run_adapter("claude", context="ctx")
    assert "claude_agent_sdk not installed" in out


def test_claude_adapter_passes_qod_constitution(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt context the daemon sends must START with the rendered QOD."""
    captured: dict[str, str] = {}

    async def fake_query(*, prompt: str, options: Any = None) -> Any:
        captured["prompt"] = prompt

        class _Msg:
            content = "noop"

        yield _Msg()

    fake_module = type(sys)("claude_agent_sdk")
    fake_module.query = fake_query
    fake_module.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    prompt = _qod_prompt()
    asyncio.run(reflexd.HeadlessAdapter(timeout_s=2).run("claude", context=prompt))
    assert captured["prompt"].startswith("# Quorus Operating Discipline (QOD)")


# ---------------------------------------------------------------------------
# Codex adapter contract — subprocess (codex exec --json --prompt)
# ---------------------------------------------------------------------------


def test_codex_adapter_invokes_correct_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex must be spawned with EXACTLY ``codex exec --json --prompt <ctx>``."""
    seen_argv: list[str] = []

    async def fake_exec(*argv: str, **_k: Any) -> _FakeProc:
        seen_argv.extend(argv)
        return _FakeProc(stdout=json.dumps({"delta": "hi codex"}).encode())

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    out = _run_adapter("codex", context="my-prompt-text")
    assert "hi codex" in out
    assert seen_argv == reflexd.build_codex_argv("my-prompt-text")
    assert seen_argv == ["codex", "exec", "--json", "--prompt", "my-prompt-text"]


def test_codex_adapter_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hanging codex process must be killed and we return a timeout sentinel."""
    proc = _FakeProc(hang=True)

    async def fake_exec(*_a: str, **_k: Any) -> _FakeProc:
        return proc

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = reflexd.HeadlessAdapter(timeout_s=0)  # immediate timeout
    out = asyncio.run(adapter.run("codex", context="ctx"))
    assert "timed out" in out
    assert proc.killed is True


def test_codex_adapter_handles_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-zero exit → 'errored' sentinel, stderr captured in log only."""
    async def fake_exec(*_a: str, **_k: Any) -> _FakeProc:
        return _FakeProc(stdout=b"", stderr=b"boom", returncode=2)

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    out = _run_adapter("codex", context="ctx")
    assert "errored" in out


def test_codex_adapter_handles_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutil.which returns None → adapter returns a clear 'not installed' sentinel."""
    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: None)

    # And ensure no actual subprocess fires if which is bypassed.
    async def must_not_run(*_a: str, **_k: Any) -> _FakeProc:  # pragma: no cover
        raise AssertionError("subprocess should not be spawned when which returns None")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", must_not_run)
    out = _run_adapter("codex", context="ctx")
    assert "codex not installed" in out


def test_codex_adapter_passes_qod_constitution(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt argument to codex must START with the rendered QOD."""
    seen_argv: list[str] = []

    async def fake_exec(*argv: str, **_k: Any) -> _FakeProc:
        seen_argv.extend(argv)
        return _FakeProc(stdout=json.dumps({"delta": "ok"}).encode())

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    prompt = _qod_prompt()
    asyncio.run(reflexd.HeadlessAdapter(timeout_s=2).run("codex", context=prompt))
    # argv = [bin, exec, --json, --prompt, <context>]
    assert seen_argv[-1].startswith("# Quorus Operating Discipline (QOD)")


# ---------------------------------------------------------------------------
# Gemini adapter contract — subprocess (gemini --prompt)
# ---------------------------------------------------------------------------


def test_gemini_adapter_invokes_correct_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini contract: ``gemini --prompt <ctx>``."""
    seen_argv: list[str] = []

    async def fake_exec(*argv: str, **_k: Any) -> _FakeProc:
        seen_argv.extend(argv)
        return _FakeProc(stdout=b"hi gemini\n")

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/gemini")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    out = _run_adapter("gemini", context="hello-gemini")
    assert out == "hi gemini"
    assert seen_argv == reflexd.build_gemini_argv("hello-gemini")
    assert seen_argv == ["gemini", "--prompt", "hello-gemini"]


def test_gemini_adapter_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc(hang=True)

    async def fake_exec(*_a: str, **_k: Any) -> _FakeProc:
        return proc

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/gemini")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = reflexd.HeadlessAdapter(timeout_s=0)
    out = asyncio.run(adapter.run("gemini", context="ctx"))
    assert "timed out" in out
    assert proc.killed is True


def test_gemini_adapter_handles_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*_a: str, **_k: Any) -> _FakeProc:
        return _FakeProc(stdout=b"", stderr=b"auth failure", returncode=1)

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/gemini")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    out = _run_adapter("gemini", context="ctx")
    assert "errored" in out


def test_gemini_adapter_handles_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: None)
    out = _run_adapter("gemini", context="ctx")
    assert "gemini not installed" in out


def test_gemini_adapter_passes_qod_constitution(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_argv: list[str] = []

    async def fake_exec(*argv: str, **_k: Any) -> _FakeProc:
        seen_argv.extend(argv)
        return _FakeProc(stdout=b"ok")

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/gemini")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    prompt = _qod_prompt()
    asyncio.run(reflexd.HeadlessAdapter(timeout_s=2).run("gemini", context=prompt))
    # argv = [gemini, --prompt, <context>]
    assert seen_argv[-1].startswith("# Quorus Operating Discipline (QOD)")


# ---------------------------------------------------------------------------
# Cursor adapter contract — subprocess + file-inbox fallback
# ---------------------------------------------------------------------------


def test_cursor_adapter_invokes_correct_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cursor contract: ``cursor-agent --headless --prompt <ctx>``."""
    seen_argv: list[str] = []

    async def fake_exec(*argv: str, **_k: Any) -> _FakeProc:
        seen_argv.extend(argv)
        return _FakeProc(stdout=b"hi cursor\n")

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/cursor-agent")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    out = _run_adapter("cursor", context="hello-cursor")
    assert out == "hi cursor"
    assert seen_argv == reflexd.build_cursor_argv("hello-cursor")
    assert seen_argv == ["cursor-agent", "--headless", "--prompt", "hello-cursor"]


def test_cursor_adapter_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc(hang=True)

    async def fake_exec(*_a: str, **_k: Any) -> _FakeProc:
        return proc

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/cursor-agent")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    adapter = reflexd.HeadlessAdapter(timeout_s=0)
    out = asyncio.run(adapter.run("cursor", context="ctx"))
    # Either the timeout sentinel surfaces, or we fall through to the inbox
    # fallback. Both are acceptable — what we MUST not see is a hang.
    assert "timed out" in out or "wrote prompt" in out
    assert proc.killed is True


def test_cursor_adapter_handles_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*_a: str, **_k: Any) -> _FakeProc:
        return _FakeProc(stdout=b"", stderr=b"crash", returncode=1)

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/cursor-agent")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    out = _run_adapter("cursor", context="ctx")
    assert "errored" in out


def test_cursor_adapter_handles_missing_binary_falls_back_to_inbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When cursor-agent is absent, write ``~/.cursor/inbox/<participant>/wake_<ts>.md``."""
    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("REFLEXD_PARTICIPANT", "arav-cursor")

    async def must_not_run(*_a: str, **_k: Any) -> _FakeProc:  # pragma: no cover
        raise AssertionError("subprocess should not run when binary is missing")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", must_not_run)

    out = _run_adapter("cursor", context="please-reply")
    assert "wrote prompt to" in out
    inbox = tmp_path / ".cursor" / "inbox" / "arav-cursor"
    assert inbox.exists()
    files = list(inbox.iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("wake_") and files[0].name.endswith(".md")
    assert files[0].read_text(encoding="utf-8") == "please-reply"


def test_cursor_adapter_passes_qod_constitution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cursor adapter must include the QOD in the prompt argv."""
    seen_argv: list[str] = []

    async def fake_exec(*argv: str, **_k: Any) -> _FakeProc:
        seen_argv.extend(argv)
        return _FakeProc(stdout=b"ok")

    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/cursor-agent")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    prompt = _qod_prompt()
    asyncio.run(reflexd.HeadlessAdapter(timeout_s=2).run("cursor", context=prompt))
    # argv = [cursor-agent, --headless, --prompt, <context>]
    assert seen_argv[-1].startswith("# Quorus Operating Discipline (QOD)")


def test_cursor_inbox_sanitises_participant_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Path-traversal in participant name must be sanitised away."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    out = reflexd.HeadlessAdapter._write_cursor_inbox("body", participant="../../etc/passwd")
    assert "wrote prompt to" in out
    # The escape chars become underscores; no parent-path traversal.
    assert "/etc/passwd" not in out
    assert (tmp_path / ".cursor" / "inbox").exists()


# ---------------------------------------------------------------------------
# Version probe contract
# ---------------------------------------------------------------------------


def test_probe_harness_version_returns_string_for_known_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the binary exists, parse the first non-empty line of --version output."""
    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/codex")

    fake_completed = type("CP", (), {"stdout": "codex 1.2.3\n", "stderr": "", "returncode": 0})()

    with patch.object(reflexd.subprocess, "run", return_value=fake_completed) as mock_run:
        ver = reflexd.probe_harness_version("codex", timeout_s=1.0)
        assert ver == "codex 1.2.3"
        # Argv must be the pinned version-probe shape.
        called_argv = mock_run.call_args[0][0]
        assert called_argv == ["codex", "--version"]


def test_probe_harness_version_returns_none_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: None)
    assert reflexd.probe_harness_version("codex") is None
    assert reflexd.probe_harness_version("gemini") is None
    assert reflexd.probe_harness_version("cursor") is None


def test_probe_harness_version_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hanging --version probe must NOT block daemon startup."""
    monkeypatch.setattr(reflexd.shutil, "which", lambda _b: "/fake/codex")

    def boom(*_a: Any, **_k: Any) -> Any:
        raise reflexd.subprocess.TimeoutExpired(cmd="codex --version", timeout=1.0)

    monkeypatch.setattr(reflexd.subprocess, "run", boom)
    assert reflexd.probe_harness_version("codex", timeout_s=0.1) is None


def test_log_adapter_versions_records_one_entry_per_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """log_adapter_versions probes claude/codex/gemini/cursor exactly once each."""
    seen: list[str] = []

    def fake_probe(harness: str) -> str | None:
        seen.append(harness)
        return None  # All "not detected" — must still log a line per harness.

    versions = reflexd.log_adapter_versions(probe=fake_probe)
    assert seen == ["claude", "codex", "gemini", "cursor"]
    assert versions == {"claude": None, "codex": None, "gemini": None, "cursor": None}


def test_log_adapter_versions_warns_on_unknown_version(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Versions outside KNOWN_GOOD_VERSIONS prefixes WARN but don't refuse to run."""
    def odd_probe(harness: str) -> str | None:
        return "99.99.99-future" if harness == "codex" else None

    with caplog.at_level("WARNING", logger="reflexd"):
        reflexd.log_adapter_versions(probe=odd_probe)
    warns = [r for r in caplog.records if r.levelname == "WARNING" and "codex" in r.message]
    assert warns, "expected a WARNING log line for unknown codex version"


# ---------------------------------------------------------------------------
# Dry-run contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "harness, expected_argv",
    [
        ("codex", ["codex", "exec", "--json", "--prompt", "ctx"]),
        ("gemini", ["gemini", "--prompt", "ctx"]),
        ("cursor", ["cursor-agent", "--headless", "--prompt", "ctx"]),
    ],
)
def test_adapter_dry_run_records_argv_without_spawning(
    monkeypatch: pytest.MonkeyPatch, harness: str, expected_argv: list[str],
) -> None:
    """In dry-run, the adapter must record argv and never call create_subprocess_exec."""
    async def must_not_run(*_a: str, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("dry-run must not spawn subprocesses")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", must_not_run)

    adapter = reflexd.HeadlessAdapter(timeout_s=2, dry_run=True)
    out = asyncio.run(adapter.run(harness, context="ctx"))
    assert "[reflexd:dry-run]" in out
    assert adapter.last_dry_run is not None
    assert adapter.last_dry_run["harness"] == harness
    assert adapter.last_dry_run["argv"] == expected_argv


def test_adapter_dry_run_claude_records_sdk_call_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry-run for claude records the SDK kwargs, not an argv list."""
    adapter = reflexd.HeadlessAdapter(timeout_s=2, dry_run=True)
    out = asyncio.run(adapter.run("claude", context="hello"))
    assert "[reflexd:dry-run]" in out
    rec = adapter.last_dry_run
    assert rec is not None
    assert rec["harness"] == "claude"
    sdk_call = rec["argv"]
    assert isinstance(sdk_call, dict)
    assert sdk_call["module"] == "claude_agent_sdk"
    assert sdk_call["callable"] == "query"
    assert sdk_call["kwargs"]["prompt"] == "hello"
    assert sdk_call["kwargs"]["options"]["max_turns"] == 1
