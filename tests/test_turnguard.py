"""Tests for ``quorus/runtime/turnguard.py`` and the ``quorus turnguard`` CLI.

PR-C2 — TurnGuard. The busy-file is the single source of truth between
the harness writers (claude/codex/gemini agents + manual hook calls)
and the reflex daemon reader. These tests pin both the file format and
the CLI exit codes the demo depends on.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import stat
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

# scripts/ is not a package — load reflexd by path so the regression test
# below can verify reflexd's busy-file logic delegates to the helper.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFLEXD_PATH = _REPO_ROOT / "scripts" / "reflexd.py"


@pytest.fixture
def runtime_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Reroute ``Path.home()`` so the helper writes under ``tmp_path``."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Force a fresh import so the constants pick up the new HOME if the
    # helper ever caches Path.home() at import time.
    if "quorus.runtime.turnguard" in sys.modules:
        importlib.reload(sys.modules["quorus.runtime.turnguard"])
    return tmp_path


@pytest.fixture
def tg(runtime_home: Path):
    from quorus.runtime import turnguard as _tg
    return _tg


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_begin_writes_busy_file_with_correct_shape(tg, runtime_home: Path) -> None:
    path = tg.begin("arav-claude", tool="Bash", ttl=60)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload.keys()) == {"started_at", "tool", "expires_at", "pid"}
    assert payload["tool"] == "Bash"
    assert payload["pid"] == os.getpid()
    # ISO8601 with timezone for both timestamps
    started = datetime.fromisoformat(payload["started_at"])
    expires = datetime.fromisoformat(payload["expires_at"])
    assert started.tzinfo is not None
    assert expires.tzinfo is not None
    assert (expires - started) >= timedelta(seconds=59)


def test_end_deletes_busy_file(tg, runtime_home: Path) -> None:
    path = tg.begin("arav-claude", tool="Edit", ttl=60)
    assert path.exists()
    tg.end("arav-claude")
    assert not path.exists()


def test_end_when_no_file_is_noop(tg) -> None:
    # Must not raise — idempotent end() is required by harness try/finally.
    tg.end("never-existed")
    tg.end("never-existed")


def test_is_busy_true_when_file_exists(tg, runtime_home: Path) -> None:
    assert tg.is_busy("arav-claude") is False
    tg.begin("arav-claude", tool="Read", ttl=120)
    assert tg.is_busy("arav-claude") is True


def test_is_busy_false_after_ttl_expires(tg, runtime_home: Path) -> None:
    path = tg.begin("arav-claude", tool="Bash", ttl=1)
    assert path.exists()
    # Sleep just past TTL so the reader treats it as stale.
    time.sleep(1.1)
    assert tg.is_busy("arav-claude") is False
    # Stale file must be auto-deleted on read so reflexd's next pass is fast.
    assert not path.exists()


def test_is_busy_false_for_unknown_participant(tg) -> None:
    assert tg.is_busy("never-heard-of-them") is False


def test_runtime_dir_perms_are_0700(tg, runtime_home: Path) -> None:
    rd = tg.runtime_dir()
    assert rd.exists()
    mode = stat.S_IMODE(rd.stat().st_mode)
    # 0700 — owner-only. Some CI filesystems may layer on extra bits but
    # the group/other bits MUST stay clear.
    assert mode & 0o077 == 0, f"runtime dir leaks to group/other: {oct(mode)}"


def test_busy_file_perms_are_0600(tg, runtime_home: Path) -> None:
    path = tg.begin("arav-claude", tool="Bash", ttl=60)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & 0o077 == 0, f"busy file leaks to group/other: {oct(mode)}"


def test_busy_file_contains_no_arguments_only_tool_name(tg, runtime_home: Path) -> None:
    """PII guard: the spec says tool NAME only, never tool arguments."""
    path = tg.begin("arav-claude", tool="Bash", ttl=60)
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Tool field must be a short name, never command args.
    assert payload["tool"] == "Bash"
    assert "rm -rf" not in path.read_text(encoding="utf-8")


def test_begin_idempotent_extends_ttl(tg, runtime_home: Path) -> None:
    """Re-running begin extends expires_at but preserves started_at."""
    path = tg.begin("arav-claude", tool="Bash", ttl=1)
    first = json.loads(path.read_text(encoding="utf-8"))
    time.sleep(0.05)
    tg.begin("arav-claude", tool="Edit", ttl=300)
    second = json.loads(path.read_text(encoding="utf-8"))
    # started_at preserved across calls.
    assert second["started_at"] == first["started_at"]
    # expires_at strictly later — TTL bumped.
    assert second["expires_at"] > first["expires_at"]
    # Tool may update.
    assert second["tool"] == "Edit"


def test_begin_rejects_invalid_ttl(tg) -> None:
    with pytest.raises(ValueError):
        tg.begin("arav-claude", tool="Bash", ttl=0)
    with pytest.raises(ValueError):
        tg.begin("arav-claude", tool="Bash", ttl=-5)


def test_busy_path_rejects_empty_participant(tg) -> None:
    with pytest.raises(ValueError):
        tg.busy_path("")
    with pytest.raises(ValueError):
        tg.busy_path("   ")


def test_turnguard_busy_path_traversal_sanitized(tg, runtime_home: Path) -> None:
    """CRIT-4: hostile participant names cannot escape the runtime dir.

    A literal ``"../../../etc/passwd"`` participant must NOT produce a
    busy-file outside ``~/.quorus/runtime/``. The path-sanitiser
    replaces every ``[^A-Za-z0-9._-]`` character with ``_``, so the
    final path stays inside the runtime dir even for hostile input.
    """
    rd = tg.runtime_dir()
    # Hostile name: classic path-traversal payload.
    p = tg.busy_path("../../../etc/passwd")
    # The result MUST be a child of the runtime dir, not /etc/passwd.
    assert str(p).startswith(str(rd)), (
        f"path escaped runtime dir: {p}"
    )
    # No literal slashes survived the sanitiser.
    assert p.parent == rd, f"unexpected parent: {p.parent}"
    # Verify writing actually lands inside the dir, not at /etc/passwd.
    written = tg.begin("../../../etc/passwd", tool="x", ttl=60)
    assert written.parent == rd, (
        f"begin() escaped runtime dir: {written}"
    )
    # The hostile filename must NOT contain any path separator. ``.`` is
    # allowed in the safe-name regex (so e.g. ``alice.dev`` works), but
    # ``/`` is not — that's the actual traversal vector.
    assert "/" not in written.name
    # The /etc/passwd file at the OS root MUST NOT have been touched.
    import os
    assert not os.path.exists("/etc/passwd.busy")


def test_turnguard_busy_path_sanitises_special_chars(tg, runtime_home: Path) -> None:
    """Various non-alphanumeric chars get replaced with ``_``."""
    rd = tg.runtime_dir()
    cases = [
        "alice;rm -rf /",
        "alice\nbob",
        "alice/etc",
        "alice\\windows",
        "alice$(whoami)",
    ]
    for name in cases:
        p = tg.busy_path(name)
        assert p.parent == rd, f"{name!r} escaped: {p}"
        assert "/" not in p.name
        assert "\\" not in p.name
        assert "\n" not in p.name
        assert ";" not in p.name
        assert "$" not in p.name


def test_busy_context_manager_clears_on_exception(tg, runtime_home: Path) -> None:
    """``with busy(...):`` must always remove the file, even on exception."""
    p = "arav-claude"
    try:
        with tg.busy(p, tool="Bash", ttl=60):
            assert tg.is_busy(p) is True
            raise RuntimeError("simulated tool crash")
    except RuntimeError:
        pass
    assert tg.is_busy(p) is False


def test_is_busy_treats_legacy_raw_text_as_busy(tg, runtime_home: Path) -> None:
    """Legacy reflexd wrote raw 'pid=N' text. We must still honour those.

    Reflexd's existing test_busyfile_skips_wake writes raw text — preserving
    that behavior is the regression-bar for this PR.
    """
    rd = tg.runtime_dir()
    legacy = rd / "arav-claude.busy"
    legacy.write_text("pid=123", encoding="utf-8")
    assert tg.is_busy("arav-claude") is True


def test_is_busy_clears_corrupt_json_with_past_ttl(tg, runtime_home: Path) -> None:
    """Files past expires_at MUST be auto-deleted on read."""
    rd = tg.runtime_dir()
    path = rd / "arav-claude.busy"
    past = (datetime.now(tz=timezone.utc) - timedelta(seconds=10)).isoformat()
    path.write_text(json.dumps({"started_at": "x", "tool": "x", "pid": 1, "expires_at": past}))
    assert tg.is_busy("arav-claude") is False
    assert not path.exists()


# ---------------------------------------------------------------------------
# CLI tests — drive ``quorus turnguard`` via main(["turnguard", ...])
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Invoke ``quorus_cli.cli.main`` with custom argv. Returns exit code."""
    from quorus_cli import cli as _cli
    monkeypatch.setattr(sys, "argv", ["quorus"] + argv)
    try:
        _cli.main()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)


def test_cli_begin_writes_file(monkeypatch: pytest.MonkeyPatch, runtime_home: Path) -> None:
    rc = _run_cli(monkeypatch, ["turnguard", "begin", "--participant", "arav-claude",
                                "--tool", "Bash", "--ttl", "60", "--quiet"])
    assert rc == 0
    busy = runtime_home / ".quorus" / "runtime" / "arav-claude.busy"
    assert busy.exists()


def test_cli_begin_idempotent_extends_ttl(
    monkeypatch: pytest.MonkeyPatch, runtime_home: Path
) -> None:
    _run_cli(monkeypatch, ["turnguard", "begin", "--participant", "arav-claude",
                           "--tool", "Bash", "--ttl", "1", "--quiet"])
    p = runtime_home / ".quorus" / "runtime" / "arav-claude.busy"
    first = json.loads(p.read_text(encoding="utf-8"))
    time.sleep(0.05)
    _run_cli(monkeypatch, ["turnguard", "begin", "--participant", "arav-claude",
                           "--tool", "Edit", "--ttl", "300", "--quiet"])
    second = json.loads(p.read_text(encoding="utf-8"))
    assert second["started_at"] == first["started_at"]
    assert second["expires_at"] > first["expires_at"]


def test_cli_end_removes_file(monkeypatch: pytest.MonkeyPatch, runtime_home: Path) -> None:
    _run_cli(monkeypatch, ["turnguard", "begin", "--participant", "arav-claude",
                           "--tool", "Bash", "--quiet"])
    p = runtime_home / ".quorus" / "runtime" / "arav-claude.busy"
    assert p.exists()
    rc = _run_cli(monkeypatch, ["turnguard", "end", "--participant", "arav-claude",
                                "--quiet"])
    assert rc == 0
    assert not p.exists()


def test_cli_end_is_noop_when_idle(
    monkeypatch: pytest.MonkeyPatch, runtime_home: Path
) -> None:
    rc = _run_cli(monkeypatch, ["turnguard", "end", "--participant", "missing",
                                "--quiet"])
    assert rc == 0


def test_cli_status_exit_codes(
    monkeypatch: pytest.MonkeyPatch, runtime_home: Path, capsys: pytest.CaptureFixture
) -> None:
    # Idle → exit 1.
    rc = _run_cli(monkeypatch, ["turnguard", "status", "--participant", "arav-claude"])
    assert rc == 1, "idle state must exit 1"

    # Begin → busy → exit 0.
    _run_cli(monkeypatch, ["turnguard", "begin", "--participant", "arav-claude",
                           "--tool", "Bash", "--ttl", "60", "--quiet"])
    rc = _run_cli(monkeypatch, ["turnguard", "status", "--participant", "arav-claude"])
    assert rc == 0, "busy state must exit 0"


def test_cli_resolves_participant_from_env(
    monkeypatch: pytest.MonkeyPatch, runtime_home: Path
) -> None:
    """No --participant flag → fall through to QUORUS_PARTICIPANT."""
    monkeypatch.setenv("QUORUS_PARTICIPANT", "arav-codex")
    rc = _run_cli(monkeypatch, ["turnguard", "begin", "--tool", "codex-exec", "--quiet"])
    assert rc == 0
    busy = runtime_home / ".quorus" / "runtime" / "arav-codex.busy"
    assert busy.exists()


# ---------------------------------------------------------------------------
# Regression: reflexd MUST use the helper, not a parallel implementation.
# ---------------------------------------------------------------------------


def _load_reflexd():
    spec = importlib.util.spec_from_file_location("reflexd", _REFLEXD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reflexd"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_reflexd_imports_from_turnguard_helper() -> None:
    """Single source of truth — reflexd must import the helper, not re-impl.

    A drift between the writer (CLI) and reader (reflexd) is the exact
    failure mode this PR is built to prevent.
    """
    src = _REFLEXD_PATH.read_text(encoding="utf-8")
    assert "from quorus.runtime.turnguard import" in src
    # The legacy hand-rolled `is_busy` body was a one-liner returning
    # `path.exists()` with no TTL handling. Pin against its return.
    reflexd = _load_reflexd()
    # Public surface preserved for backward-compat with the CLI/MCP callers.
    assert callable(reflexd.is_busy)
    assert callable(reflexd.busy_path)


def test_reflexd_is_busy_honours_helper_ttl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reflexd's is_busy(participant, runtime_dir) routes through the helper.

    Stale TTL files (expired) must auto-clear on read — exactly matching
    the helper's semantics.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    reflexd = _load_reflexd()

    rt = tmp_path / "runtime"
    rt.mkdir()
    # Write a TTL-expired file via the helper to exercise the real path.
    from quorus.runtime import turnguard as _tg
    p = _tg.busy_path("arav-claude", dir_override=rt)
    past = (datetime.now(tz=timezone.utc) - timedelta(seconds=10)).isoformat()
    p.write_text(json.dumps({"started_at": "x", "tool": "x", "pid": 1, "expires_at": past}))
    assert reflexd.is_busy("arav-claude", rt) is False
    assert not p.exists(), "reflexd must clear stale file via the helper"


def test_reflexd_is_busy_still_honours_legacy_raw_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The original test_reflexd.test_busyfile_skips_wake writes raw text.

    Preserving that path means the helper accepts raw text as 'busy' so
    older harness writes still gate reflexd correctly.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    reflexd = _load_reflexd()
    rt = tmp_path / "runtime"
    rt.mkdir()
    (rt / "arav-claude.busy").write_text("pid=123", encoding="utf-8")
    assert reflexd.is_busy("arav-claude", rt) is True


# ---------------------------------------------------------------------------
# Smoke that the agent harnesses import the helper.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    ["quorus_cli.codex_agent", "quorus_cli.claude_agent", "quorus_cli.gemini_agent"],
)
def test_agent_modules_import_turnguard(module_name: str) -> None:
    """Wire-up regression: the three loops we control must own the busy-file."""
    mod: Any = importlib.import_module(module_name)
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from quorus.runtime import turnguard" in src
    assert "_turnguard.busy(" in src
