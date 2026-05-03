"""Regression tests for the user-visible status bar after a failed send.

Bug report (2026-05-02): when the relay returned 403 "Cannot send as
another user" (anti-impersonation guard), the TUI showed "Couldn't reach
the relay. Is it running?" — a lie. The relay was up; it was refusing
the message because chat_identity was being sent on the wire instead of
agent_name. The misleading hint sent users on a wild goose chase
checking their relay process when the actual fix was server-side.

The fix preserves the generic hint for genuine reachability problems
(5xx, ConnectError, timeouts) and surfaces the relay's `detail` field
for 4xx rejections.

Bug report (2026-05-02): the header showed `@arav-codex` even when the
profile had `chat_identity=arav`. The send path was correctly fixed in
a1cc996 (the wire stays on agent_name due to relay anti-impersonation),
but the display layer was forgotten — the user saw their own messages
as @arav-codex in the header even though the bubble showed @arav.

These tests pin both fixes so they don't regress.
"""

from __future__ import annotations

from unittest.mock import patch

from rich.console import Console

from quorus import tui_hub
from quorus.tui_hub import _render_header, _send_message

# ---------------------------------------------------------------------------
# Bug 1 — _send_message surfaces relay rejection reason on 4xx
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no body")
        return self._json


class TestSendMessageSurfacesRelayError:
    def test_403_with_detail_sets_last_send_error(self):
        """403 + detail → _LAST_SEND_ERROR carries the actual reason."""
        fake = _FakeResponse(
            status_code=403,
            json_data={"detail": "Cannot send as another user"},
        )
        with patch("quorus_tui.hub.httpx.post", return_value=fake):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav-codex", "hello"
            )
        assert sent_id is None
        assert tui_hub._LAST_SEND_ERROR == "Cannot send as another user"

    def test_404_with_detail_sets_last_send_error(self):
        fake = _FakeResponse(
            status_code=404,
            json_data={"detail": "Room not found"},
        )
        with patch("quorus_tui.hub.httpx.post", return_value=fake):
            sent_id = _send_message(
                "http://relay", "secret", "ghost", "arav", "hi"
            )
        assert sent_id is None
        assert tui_hub._LAST_SEND_ERROR == "Room not found"

    def test_400_without_detail_falls_back_to_generic_4xx_message(self):
        """4xx without a detail field still surfaces something useful."""
        fake = _FakeResponse(status_code=400, json_data={})
        with patch("quorus_tui.hub.httpx.post", return_value=fake):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav", "hi"
            )
        assert sent_id is None
        assert tui_hub._LAST_SEND_ERROR is not None
        assert "400" in tui_hub._LAST_SEND_ERROR

    def test_500_leaves_last_send_error_as_none(self):
        """5xx is a genuine reachability problem — keep the generic hint."""
        fake = _FakeResponse(status_code=500, json_data={"detail": "ouch"})
        with patch("quorus_tui.hub.httpx.post", return_value=fake):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav", "hi"
            )
        assert sent_id is None
        assert tui_hub._LAST_SEND_ERROR is None

    def test_connection_error_leaves_last_send_error_as_none(self):
        """Network exception → keep the generic 'is it running' hint."""
        with patch(
            "quorus_tui.hub.httpx.post", side_effect=Exception("ECONNREFUSED")
        ):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav", "hi"
            )
        assert sent_id is None
        assert tui_hub._LAST_SEND_ERROR is None

    def test_success_resets_last_send_error(self):
        """A successful send must clear stale error from a prior 4xx."""
        # Prime the global with a stale error.
        tui_hub._LAST_SEND_ERROR = "stale 403 message"
        fake = _FakeResponse(status_code=200, json_data={"id": "abc"})
        with patch("quorus_tui.hub.httpx.post", return_value=fake):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav", "hi"
            )
        assert sent_id == "abc"
        assert tui_hub._LAST_SEND_ERROR is None

    def test_each_call_resets_last_send_error_first(self):
        """A new send clears the prior call's error before evaluating its own."""
        tui_hub._LAST_SEND_ERROR = "prior error"
        # Connection error path — does NOT set _LAST_SEND_ERROR, but
        # the reset-on-entry must still clear the stale value.
        with patch(
            "quorus_tui.hub.httpx.post", side_effect=Exception("boom")
        ):
            _send_message("http://relay", "secret", "dev", "arav", "hi")
        assert tui_hub._LAST_SEND_ERROR is None


class TestSendErrorIsWiredToStatusBar:
    """Pin the call site so the fix doesn't get reverted."""

    def test_caller_reads_last_send_error_on_none_return(self):
        """The main input loop must read _LAST_SEND_ERROR before falling back."""
        import inspect

        src = inspect.getsource(tui_hub)
        # Find the send-then-status-bar block.
        anchor = '"Couldn\'t reach the relay. Is it running?'
        assert anchor in src, "generic-hint string moved — re-pin the test"
        # The check for _LAST_SEND_ERROR must happen on the failure
        # branch — i.e., somewhere between the `else:` after the
        # successful send and the generic-hint string.
        idx = src.index(anchor)
        # Look back ~600 chars; _LAST_SEND_ERROR must appear in that region.
        window = src[max(0, idx - 600) : idx]
        assert "_LAST_SEND_ERROR" in window, (
            "main loop no longer consults _LAST_SEND_ERROR before showing "
            "the generic hint — relay 4xx errors will lie about "
            "reachability again"
        )


# ---------------------------------------------------------------------------
# Bug 2 — header uses chat_identity when set
# ---------------------------------------------------------------------------


def _render_to_str(text) -> str:
    """Render a Rich Text to plain string for assertion."""
    buf = Console(file=__import__("io").StringIO(), width=120, no_color=True)
    buf.print(text)
    return buf.file.getvalue()


class TestHeaderUsesChatIdentity:
    def test_chat_identity_overrides_agent_name_in_header(self):
        out = _render_to_str(
            _render_header(
                "http://relay.example",
                "arav-codex",
                connected=True,
                status="",
                chat_identity="arav",
                console_width=120,
            )
        )
        assert "@arav" in out
        # Strict check: don't accept @arav as a substring of @arav-codex.
        assert "@arav-codex" not in out, (
            "header still shows @arav-codex even when chat_identity=arav — "
            "the display layer was not flipped"
        )

    def test_falls_back_to_agent_name_when_chat_identity_empty(self):
        out = _render_to_str(
            _render_header(
                "http://relay.example",
                "arav-codex",
                connected=True,
                status="",
                chat_identity="",
                console_width=120,
            )
        )
        assert "@arav-codex" in out

    def test_falls_back_to_agent_name_when_chat_identity_omitted(self):
        """No chat_identity kwarg → default empty string → use agent_name."""
        out = _render_to_str(
            _render_header(
                "http://relay.example",
                "claude-pm",
                connected=True,
                status="",
                console_width=120,
            )
        )
        assert "@claude-pm" in out

    def test_main_loop_passes_chat_identity_to_header(self):
        """Pin the wiring so the kwarg actually flows through."""
        import inspect

        src = inspect.getsource(tui_hub)
        # Two occurrences exist: the def site and the call site. Skip
        # the def to land on the call (inside the main render block).
        def_idx = src.index("def _render_header(")
        # `def _render_header(` itself contains the substring; advance past
        # the closing paren of the def's signature so the next .index()
        # finds the actual call.
        after_def_signature = src.index(") -> Text:", def_idx)
        call_idx = src.index("_render_header(", after_def_signature)
        block = src[call_idx : call_idx + 800]
        assert "chat_identity=chat_identity" in block, (
            "main render no longer passes chat_identity to _render_header — "
            "header will revert to @arav-codex display"
        )


# ---------------------------------------------------------------------------
# Bug 3 — _send_message retries transient 5xx / connection failures
# ---------------------------------------------------------------------------


class TestSendMessageRetriesTransientFailures:
    """When the relay is restarting or returns a transient 5xx, the TUI
    used to surface 'Couldn't reach the relay' on the very first failure.
    The retry loop ([0.2, 0.5, 1.0] s, max 1.7 s total) recovers without
    bothering the user.

    Constraints pinned by these tests:
      - 4xx is NEVER retried (real client errors)
      - Bare Exception is NEVER retried (unknown — fail closed)
      - 5xx and httpx.ConnectError/ReadTimeout/RemoteProtocolError ARE retried
      - On eventual success, _LAST_SEND_ERROR stays None (5xx path)
    """

    def test_retry_recovers_when_second_attempt_returns_201(self, monkeypatch):
        """503 then 201 → returns the success ID and never sets _LAST_SEND_ERROR."""
        monkeypatch.setattr(tui_hub.time, "sleep", lambda _s: None)
        responses = [
            _FakeResponse(status_code=503, json_data={"detail": "restarting"}),
            _FakeResponse(status_code=201, json_data={"id": "msg-after-retry"}),
        ]
        calls = {"n": 0}

        def fake_post(*_a, **_kw):
            r = responses[calls["n"]]
            calls["n"] += 1
            return r

        with patch("quorus_tui.hub.httpx.post", side_effect=fake_post):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav-codex", "hello"
            )
        assert sent_id == "msg-after-retry"
        assert calls["n"] == 2, "retry loop should have fired exactly once"
        # 5xx must NOT poison the global error — generic-hint semantics intact.
        assert tui_hub._LAST_SEND_ERROR is None

    def test_retry_gives_up_after_four_attempts_on_persistent_5xx(self, monkeypatch):
        """4 failed attempts → returns None, _LAST_SEND_ERROR still None."""
        monkeypatch.setattr(tui_hub.time, "sleep", lambda _s: None)
        fake = _FakeResponse(status_code=502, json_data={"detail": "bad gateway"})
        calls = {"n": 0}

        def fake_post(*_a, **_kw):
            calls["n"] += 1
            return fake

        with patch("quorus_tui.hub.httpx.post", side_effect=fake_post):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav", "hi"
            )
        assert sent_id is None
        assert calls["n"] == 4, "expected 1 initial attempt + 3 retries"
        assert tui_hub._LAST_SEND_ERROR is None

    def test_4xx_is_not_retried(self, monkeypatch):
        """A 403 must short-circuit the retry loop — that's a real client error."""
        monkeypatch.setattr(tui_hub.time, "sleep", lambda _s: None)
        fake = _FakeResponse(
            status_code=403, json_data={"detail": "Cannot send as another user"}
        )
        calls = {"n": 0}

        def fake_post(*_a, **_kw):
            calls["n"] += 1
            return fake

        with patch("quorus_tui.hub.httpx.post", side_effect=fake_post):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav-codex", "hello"
            )
        assert sent_id is None
        assert calls["n"] == 1, "4xx must NOT be retried"
        assert tui_hub._LAST_SEND_ERROR == "Cannot send as another user"

    def test_httpx_connect_error_is_retried(self, monkeypatch):
        """httpx.ConnectError on first attempt, success on second."""
        import httpx as _httpx

        monkeypatch.setattr(tui_hub.time, "sleep", lambda _s: None)
        success = _FakeResponse(status_code=201, json_data={"id": "after-connerr"})
        calls = {"n": 0}

        def fake_post(*_a, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _httpx.ConnectError("relay starting up")
            return success

        with patch("quorus_tui.hub.httpx.post", side_effect=fake_post):
            sent_id = _send_message(
                "http://relay", "secret", "dev", "arav", "hi"
            )
        assert sent_id == "after-connerr"
        assert calls["n"] == 2
