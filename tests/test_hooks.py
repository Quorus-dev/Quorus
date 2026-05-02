"""Hook reliability tests.

Focus: the silent-error swallow at ``packages/cli/quorus_cli/hooks.py:106``
hid the cold-install smoke regression that overwrote user host configs for
weeks. The fix is to:

  1. Keep returning empty / no-op JSON on relay failure (existing intent).
  2. Append one line to ``~/.quorus/hook-debug.log`` so a future regression
     is observable without grepping process logs.
  3. Cap the log at 10 MB with a half-truncate rotation so it can't grow
     unbounded.

Tests below assert each of those three properties.
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx
from quorus_cli import hooks


class _FakeHome:
    """Helper to redirect ``Path.home()`` for the duration of a test."""

    def __init__(self, base: Path) -> None:
        self._patch = patch.object(hooks.Path, "home", return_value=base)

    def __enter__(self) -> None:
        self._patch.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self._patch.stop()


class HookDebugLogTests(unittest.TestCase):
    """Verifies ``_hook_debug_log`` writes structured lines and rotates."""

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="quorus-hooks-test-")
        self._home = Path(self._tmp)
        self._home_ctx = _FakeHome(self._home)
        self._home_ctx.__enter__()

    def tearDown(self) -> None:
        self._home_ctx.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _log_path(self) -> Path:
        return self._home / ".quorus" / "hook-debug.log"

    def test_writes_one_line_with_timestamp_and_exception(self) -> None:
        exc = RuntimeError("relay unreachable")
        hooks._hook_debug_log("test-category", exc, extra="extra-detail")

        log = self._log_path()
        self.assertTrue(log.exists(), "hook-debug.log was not created")
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        line = lines[0]
        # Lines look like:
        #   2026-05-02T18:42:11Z test-category RuntimeError: relay unreachable extra-detail
        self.assertIn("test-category", line)
        self.assertIn("RuntimeError", line)
        self.assertIn("relay unreachable", line)
        self.assertIn("extra-detail", line)
        # ISO-ish UTC timestamp prefix
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ")

    def test_rotates_when_log_exceeds_10mb(self) -> None:
        log = self._log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        # Seed a >10 MB log so the next write triggers rotation.
        big = b"X" * (hooks._HOOK_DEBUG_LOG_BYTES + 4096)
        log.write_bytes(big)
        original_size = log.stat().st_size
        self.assertGreater(original_size, hooks._HOOK_DEBUG_LOG_BYTES)

        hooks._hook_debug_log("rotation-test", RuntimeError("trigger"))

        rotated_size = log.stat().st_size
        # After rotation we keep ~half + the new line — must be smaller than
        # what we started with.
        self.assertLess(rotated_size, original_size)
        self.assertLessEqual(rotated_size, hooks._HOOK_DEBUG_LOG_BYTES)
        # New line must still be present at the tail.
        tail = log.read_bytes()[-200:].decode("utf-8", errors="replace")
        self.assertIn("rotation-test", tail)

    def test_log_failure_never_raises(self) -> None:
        """If the log can't be written, hook code must not crash."""
        # Point ``Path.home()`` at a file (not a dir) so mkdir fails.
        bogus_file = self._home / "not-a-dir"
        bogus_file.write_bytes(b"oops")
        self._home_ctx.__exit__(None, None, None)
        with patch.object(hooks.Path, "home", return_value=bogus_file):
            # Must not raise even though the path is invalid.
            try:
                hooks._hook_debug_log("path-broken", RuntimeError("x"))
            except Exception as exc:
                self.fail(f"_hook_debug_log raised: {exc!r}")
        # Re-enter the original context for tearDown.
        self._home_ctx.__enter__()


class FetchUnreadOnRelayFailureTests(unittest.TestCase):
    """``_fetch_unread`` must return [] AND log when the relay is unreachable."""

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="quorus-hooks-test-")
        self._home = Path(self._tmp)
        self._home_ctx = _FakeHome(self._home)
        self._home_ctx.__enter__()

    def tearDown(self) -> None:
        self._home_ctx.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _log_path(self) -> Path:
        return self._home / ".quorus" / "hook-debug.log"

    def test_returns_empty_on_connection_error(self) -> None:
        with patch("quorus_cli.hooks.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("relay down")
            result = hooks._fetch_unread("http://localhost:9999", "secret", "alice")
        self.assertEqual(result, [], "must return [] on relay unreachable")

    def test_logs_a_line_on_connection_error(self) -> None:
        with patch("quorus_cli.hooks.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("relay down")
            hooks._fetch_unread("http://localhost:9999", "secret", "alice")

        log = self._log_path()
        self.assertTrue(
            log.exists(),
            "hook-debug.log must be created when the relay errors",
        )
        content = log.read_text(encoding="utf-8")
        # The single line should identify the failure mode AND the agent
        # so an operator can grep for it.
        self.assertIn("fetch-unread-exc", content)
        self.assertIn("ConnectError", content)
        self.assertIn("alice", content)

    def test_logs_a_line_on_non_200_peek(self) -> None:
        # Mock a peek that returns 503 — must log and return [].
        class _Resp:
            status_code = 503

            def json(self) -> dict:
                return {}

        with patch("quorus_cli.hooks.httpx.get", return_value=_Resp()):
            result = hooks._fetch_unread(
                "http://localhost:9999", "secret", "alice",
            )
        self.assertEqual(result, [])

        log = self._log_path()
        self.assertTrue(log.exists())
        content = log.read_text(encoding="utf-8")
        self.assertIn("fetch-peek-non200", content)
        self.assertIn("status=503", content)


class GeminiBeforeAgentRelayFailureTests(unittest.TestCase):
    """End-to-end: Gemini hook entrypoint with a dead relay.

    Asserts the existing public contract holds:
      - exit code 0
      - stdout is valid JSON: {"hookSpecificOutput": {"additionalContext": ""}}
      - hook-debug.log gains at least one line about the failure
    """

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="quorus-hooks-test-")
        self._home = Path(self._tmp)
        self._home_ctx = _FakeHome(self._home)
        self._home_ctx.__enter__()

    def tearDown(self) -> None:
        self._home_ctx.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _log_path(self) -> Path:
        return self._home / ".quorus" / "hook-debug.log"

    def test_handle_gemini_beforeagent_with_dead_relay(self) -> None:
        # Patch _config to return a deterministic dead relay URL.
        with patch(
            "quorus_cli.hooks._config",
            return_value=("http://localhost:1", "secret", "alice"),
        ), patch("quorus_cli.hooks.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("dead")

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = hooks.handle_gemini_beforeagent()

        self.assertEqual(rc, 0, "hook must not block the host harness")

        # stdout must be valid JSON in the Gemini-expected shape.
        payload = json.loads(buf.getvalue().strip())
        self.assertIn("hookSpecificOutput", payload)
        self.assertIn("additionalContext", payload["hookSpecificOutput"])
        self.assertEqual(payload["hookSpecificOutput"]["additionalContext"], "")

        # And the log must have gained at least one line.
        log = self._log_path()
        self.assertTrue(log.exists())
        content = log.read_text(encoding="utf-8")
        self.assertIn("fetch-unread-exc", content)


if __name__ == "__main__":
    unittest.main()
