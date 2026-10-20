"""A retried turn doesn't post the same thing twice (bug bash, `quorus demo` on
0.6.2: the reviewer's "plan: inspect commit 5c3bfb7…" came at 73 s and 83 s).
A wake runs its harness again after a failed resume or a free model's rate
limit, each run with a new MCP server, so the room's history is the check."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from quorus_mcp.repost import WINDOW_S, duplicate_of

import quorus.mcp_server as mcp_server

NOW = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)


def _msg(sender: str, content: str, seconds_ago: float, mid: str = "m") -> dict:
    ts = (NOW - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")
    return {"id": mid, "from_name": sender, "content": content, "timestamp": ts}


def test_the_same_text_within_the_window_is_a_repeat() -> None:
    hist = [_msg("rev", "plan: inspect commit 5c3bfb7", 10, "p1")]
    assert duplicate_of(hist, "rev", "plan: inspect commit 5c3bfb7 ", NOW)["id"] == "p1"
    assert duplicate_of(hist, "other", "plan: inspect commit 5c3bfb7", NOW) is None
    old = [_msg("rev", "looks good", WINDOW_S + 5)]
    assert duplicate_of(old, "rev", "looks good", NOW) is None


def test_a_reworded_plan_after_an_unanswered_plan_is_a_repeat() -> None:
    """The next free model words it differently; the turn is the same."""
    hist = [_msg("rev", "plan: inspect commit 5c3bfb7 and run its tests", 10, "p1")]
    assert duplicate_of(hist, "rev", "Plan: review 5c3bfb7 (calc.py)", NOW)["id"] == "p1"


def test_a_plan_after_an_answer_is_a_new_turn() -> None:
    hist = [_msg("rev", "plan: inspect 5c3bfb7", 120, "p1"),
            _msg("rev", "✅ /vote approve 5c3bfb7", 60, "a1")]
    assert duplicate_of(hist, "rev", "plan: inspect 9d1e2f0", NOW) is None
    stale = [_msg("rev", "plan: inspect 5c3bfb7", WINDOW_S + 5)]
    assert duplicate_of(stale, "rev", "plan: inspect 9d1e2f0", NOW) is None


def test_other_senders_and_order_dont_matter() -> None:
    hist = [_msg("rev", "plan: a", 30, "p1"), _msg("alice", "go ahead", 20, "h1")]
    assert duplicate_of(list(reversed(hist)), "rev", "plan: b", NOW)["id"] == "p1"
    assert duplicate_of([{"from_name": "rev", "content": "x", "timestamp": "nope"}],
                        "rev", "x", NOW) is None


def _client(history) -> AsyncMock:
    def resp(body):
        r = MagicMock()
        r.json.return_value = body
        r.raise_for_status = MagicMock()
        return r

    c = AsyncMock()
    c.is_closed = False
    c.get = AsyncMock(return_value=resp(history))
    c.post = AsyncMock(return_value=resp({"id": "new-1"}))
    return c


@contextmanager
def _tool(client: AsyncMock):
    """The tool against *client*, as `rev`, with a fixed bearer: other tests leave
    the MCP module's identity and credentials in other states."""
    with patch("quorus.mcp_server._get_http_client", return_value=client), \
            patch("quorus.mcp_server.INSTANCE_NAME", "rev"), \
            patch("quorus.mcp_server._auth_headers_async",
                  AsyncMock(return_value={"Authorization": "Bearer t"})):
        yield


async def test_the_tool_skips_a_repeat_and_says_so() -> None:
    now = datetime.now(timezone.utc).isoformat()
    c = _client([{"id": "p1", "from_name": "rev", "content": "plan: inspect 5c3bfb7",
                  "timestamp": now}])
    with _tool(c):
        out = await mcp_server._send_room_message("build", "plan: review 5c3bfb7 again")
    assert "Already posted" in out and "p1" in out
    assert not _message_posts(c)


async def test_the_tool_posts_when_history_cant_be_read() -> None:
    c = _client("not a history")
    with _tool(c):
        out = await mcp_server._send_room_message("build", "plan: inspect 5c3bfb7")
    assert "new-1" in out
    assert len(_message_posts(c)) == 1


def _message_posts(c: AsyncMock) -> list:
    """Posts of room messages (the audit hook posts too)."""
    return [call for call in c.post.call_args_list if call.args[0].endswith("/messages")]
