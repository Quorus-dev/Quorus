"""Tests for :mod:`quorus.notifications.native`.

Pin the safety-critical behaviour:

* The macOS dispatch uses an argv list with the AppleScript as a literal
  ``-e`` arg — NEVER string-interpolated user content.
* The Linux dispatch shells out to ``notify-send`` via argv.
* Unsupported platforms log a content-free line to disk and return False.
* Per-(sender, room) 5-second rate limit is enforced.
* Control characters are stripped from inputs.
* Bodies > 200 chars are truncated.
* The PII guard: message content NEVER appears in the on-disk state /
  fallback log, only sender + room + counters.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from quorus.notifications import native


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Isolated state dir per-test so the rate-limiter doesn't leak."""
    d = tmp_path / "quorus"
    d.mkdir()
    return d


@pytest.fixture
def fake_run() -> MagicMock:
    """A subprocess.run mock that always returns rc=0."""
    m = MagicMock()
    m.return_value = types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    return m


# ---------------------------------------------------------------------------
# macOS dispatch — argv shape
# ---------------------------------------------------------------------------


def test_macos_uses_osascript_argv(state_dir: Path, fake_run: MagicMock) -> None:
    """macOS dispatch must use argv list, never shell=True or string-interp."""
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        ok = native.notify(
            "Quorus — dev",
            "alice: shipping today",
            sender="alice",
            room="dev",
            state_dir=state_dir,
        )
    assert ok is True
    fake_run.assert_called_once()
    argv, _kwargs = fake_run.call_args[0], fake_run.call_args[1]
    cmd = argv[0]
    # argv must start with osascript and have -e as its first option.
    assert cmd[0] == "osascript"
    assert cmd[1] == "-e"
    # The AppleScript body must be the literal `on run argv` script —
    # NOT a string with interpolated content.
    assert cmd[2].startswith("on run argv")
    assert "alice" not in cmd[2]  # sender NOT in script body
    assert "shipping" not in cmd[2]  # message NOT in script body
    # The `--` sentinel separates osascript flags from argv passed to the script.
    assert "--" in cmd
    sep = cmd.index("--")
    # After --: title, sender (subtitle), body, sound — in that order.
    after = cmd[sep + 1:]
    assert after[0] == "Quorus — dev"
    assert after[1] == "alice"
    assert after[2] == "alice: shipping today"
    assert after[3] == "Glass"
    # capture_output and timeout pinned.
    assert _kwargs.get("capture_output") is True
    assert _kwargs.get("timeout") == native.SUBPROCESS_TIMEOUT_S


def test_macos_argv_no_sound_passes_empty_string(state_dir: Path) -> None:
    """sound=False → argv slot is empty string so AppleScript skips sound block."""
    fake = MagicMock(return_value=types.SimpleNamespace(returncode=0))
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake):
        native.notify("t", "b", sender="a", room="r", sound=False, state_dir=state_dir)
    argv = fake.call_args[0][0]
    assert argv[-1] == ""  # last positional arg is the sound-name slot, empty


# ---------------------------------------------------------------------------
# Linux dispatch — argv shape
# ---------------------------------------------------------------------------


def test_linux_uses_notify_send_argv(state_dir: Path, fake_run: MagicMock) -> None:
    """Linux dispatch must shell out to notify-send via argv list."""
    with patch.object(native.sys, "platform", "linux"), \
         patch.object(native.subprocess, "run", fake_run):
        ok = native.notify(
            "Quorus — dev", "bob: hey",
            sender="bob", room="dev", state_dir=state_dir,
        )
    assert ok is True
    cmd = fake_run.call_args[0][0]
    assert cmd[0] == "notify-send"
    assert "--app-name=Quorus" in cmd
    assert "--urgency=normal" in cmd
    # title, body must be the LAST two positional args.
    assert cmd[-2] == "Quorus — dev"
    assert cmd[-1] == "bob: hey"


# ---------------------------------------------------------------------------
# Windows / unsupported — fallback to log file
# ---------------------------------------------------------------------------


def test_windows_logs_to_file(state_dir: Path) -> None:
    """On unsupported platforms, write a content-free line to a log file."""
    with patch.object(native.sys, "platform", "win32"):
        ok = native.notify(
            "Quorus — dev",
            "carol: hi",
            sender="carol",
            room="dev",
            state_dir=state_dir,
        )
    assert ok is False
    log = state_dir / native.LOG_FILE_NAME
    assert log.exists()
    line = log.read_text().strip()
    entry = json.loads(line)
    assert entry["sender"] == "carol"
    assert entry["room"] == "dev"
    # PII GUARD: content must NEVER reach the log file.
    assert "carol: hi" not in line
    assert "hi" not in entry.values()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_blocks_second_within_5s(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """Two notifications for the same (sender, room) within 5s → second is dropped."""
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        ok1 = native.notify(
            "t1", "b1", sender="alice", room="dev",
            state_dir=state_dir, now=1000.0,
        )
        ok2 = native.notify(
            "t2", "b2", sender="alice", room="dev",
            state_dir=state_dir, now=1003.0,  # 3s later — within window
        )
    assert ok1 is True
    assert ok2 is False
    # Only ONE subprocess call.
    assert fake_run.call_count == 1


def test_rate_limit_resets_after_window(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """After 5s+ elapsed, the rate limit is cleared and the second fires."""
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        ok1 = native.notify(
            "t", "b", sender="alice", room="dev",
            state_dir=state_dir, now=1000.0,
        )
        ok2 = native.notify(
            "t", "b", sender="alice", room="dev",
            state_dir=state_dir, now=1006.0,  # 6s later — past window
        )
    assert ok1 is True
    assert ok2 is True
    assert fake_run.call_count == 2


def test_rate_limit_independent_per_sender_room(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """Different (sender, room) tuples don't share a window."""
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        a = native.notify("t", "b", sender="alice", room="dev",
                          state_dir=state_dir, now=1000.0)
        b = native.notify("t", "b", sender="bob", room="dev",
                          state_dir=state_dir, now=1000.5)
        c = native.notify("t", "b", sender="alice", room="design",
                          state_dir=state_dir, now=1001.0)
    assert (a, b, c) == (True, True, True)
    assert fake_run.call_count == 3


# ---------------------------------------------------------------------------
# Sanitisation + truncation
# ---------------------------------------------------------------------------


def test_input_sanitization_strips_control_chars(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """ANSI / NUL / DEL / newlines etc. removed before reaching the OS call."""
    nasty = "hi\x00\x07\x1bworld\nnext\rline\x7f"
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        native.notify(
            "title\x00", nasty, sender="alice", room="dev",
            state_dir=state_dir,
        )
    cmd = fake_run.call_args[0][0]
    sep = cmd.index("--")
    title, _sender, body, _sound = cmd[sep + 1:]
    # All control chars removed; whitespace remnants OK.
    for c in "\x00\x07\x1b\x7f\n\r":
        assert c not in title
        assert c not in body
    # The actual content survives.
    assert "hi" in body
    assert "world" in body


def test_long_body_truncated_to_200(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """Body >200 chars gets sliced to MAX_BODY_LEN with an ellipsis."""
    long_body = "x" * 500
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        native.notify(
            "t", long_body, sender="alice", room="dev",
            state_dir=state_dir,
        )
    cmd = fake_run.call_args[0][0]
    sep = cmd.index("--")
    body = cmd[sep + 1 + 2]  # after title, sender → body
    assert len(body) == native.MAX_BODY_LEN  # 199 chars + ellipsis = 200
    assert body.endswith("…")


def test_long_title_truncated_to_80(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """Title >80 chars also gets sliced."""
    long_title = "y" * 200
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        native.notify(
            long_title, "b", sender="alice", room="dev",
            state_dir=state_dir,
        )
    cmd = fake_run.call_args[0][0]
    sep = cmd.index("--")
    title = cmd[sep + 1]
    assert len(title) == native.MAX_TITLE_LEN
    assert title.endswith("…")


# ---------------------------------------------------------------------------
# PII guard — content must never touch disk
# ---------------------------------------------------------------------------


def test_no_message_content_in_log(state_dir: Path) -> None:
    """Regression: the ENTIRE message body must never reach any state file."""
    secret_phrase = "PHI_PATIENT_SSN_123456789"
    # Trigger the fallback path (windows) so a state + log line is written.
    with patch.object(native.sys, "platform", "win32"):
        native.notify(
            f"alert: {secret_phrase}",
            f"body: {secret_phrase}",
            sender="alice",
            room=f"room-{secret_phrase}",
            state_dir=state_dir,
        )
    # The log file gets the room because that's a routing dimension —
    # but the title and body content must NOT appear.
    log = (state_dir / native.LOG_FILE_NAME).read_text()
    state = (state_dir / native.STATE_FILE_NAME).read_text()
    assert "body:" not in log
    assert "alert:" not in log
    assert "body:" not in state
    assert "alert:" not in state


def test_no_message_content_in_state_file(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """The on-disk rate-limit state must contain no body/title text."""
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        native.notify(
            "EARLOCKETHOTEPSYL",  # rare token unlikely to clash with anything
            "MILLENNIUMHAWKBOLT",
            sender="alice",
            room="dev",
            state_dir=state_dir,
        )
    state = (state_dir / native.STATE_FILE_NAME).read_text()
    assert "EARLOCKETHOTEPSYL" not in state
    assert "MILLENNIUMHAWKBOLT" not in state
    # But the routing dimensions ARE present.
    assert "alice" in state
    assert "dev" in state


# ---------------------------------------------------------------------------
# State file perms
# ---------------------------------------------------------------------------


def test_state_file_has_owner_only_perms(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """0o600 — the state file must not be world-readable."""
    if sys.platform == "win32":
        pytest.skip("POSIX perms only")
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        native.notify(
            "t", "b", sender="alice", room="dev", state_dir=state_dir,
        )
    state = state_dir / native.STATE_FILE_NAME
    assert state.exists()
    mode = state.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_subprocess_failure_returns_false(state_dir: Path) -> None:
    """osascript missing or non-zero rc → notify() returns False (never raises)."""
    fake = MagicMock(side_effect=FileNotFoundError("osascript"))
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake):
        ok = native.notify(
            "t", "b", sender="a", room="r", state_dir=state_dir,
        )
    assert ok is False


def test_empty_inputs_short_circuit(state_dir: Path) -> None:
    """Both title and body empty → no dispatch, returns False fast."""
    fake = MagicMock()
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake):
        ok = native.notify("", "", sender="a", room="r", state_dir=state_dir)
    assert ok is False
    fake.assert_not_called()


def test_corrupt_state_file_recovers(
    state_dir: Path, fake_run: MagicMock,
) -> None:
    """A corrupt state.json must NOT prevent notifications — it resets."""
    bad_state = state_dir / native.STATE_FILE_NAME
    bad_state.write_text("{not valid json")
    with patch.object(native.sys, "platform", "darwin"), \
         patch.object(native.subprocess, "run", fake_run):
        ok = native.notify(
            "t", "b", sender="a", room="r", state_dir=state_dir,
        )
    assert ok is True
