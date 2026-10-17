""""While you were away" on the home screen (2026-10-17)."""

from pathlib import Path

from quorus_tui import catchup


def _m(sender: str, content: str, ts: str) -> dict:
    return {"from_name": sender, "content": content, "timestamp": ts}


HIST = [
    _m("a-claude", "old news\n(quorus) published 1 commit(s) from x to main",
       "2026-10-16T09:00:00Z"),
    _m("arav", "@a-claude fix the login bug", "2026-10-16T11:00:00Z"),
    _m("a-claude", "✅ fixed\n(quorus) published 2 commit(s) from x to main",
       "2026-10-16T11:05:00Z"),
    _m("a-codex", "@arav should the token expire in 1h or 24h?", "2026-10-16T11:06:00Z"),
    _m("a-gemini", "⚠ gemini: quota", "2026-10-16T11:07:00Z"),
    _m("a-codex", "@arav-claude looks good", "2026-10-16T11:08:00Z"),
]
SINCE = catchup._ts("2026-10-16T10:00:00Z")


def test_digest_counts_only_what_happened_since_and_not_mine() -> None:
    d = catchup.room_digest("build", HIST, me="arav", since=SINCE)
    assert d.messages == 4  # the old one and arav's own are skipped
    assert d.shipped == 2
    assert d.needs_you == [("a-codex", "@arav should the token expire in 1h or 24h?")]
    assert d.stuck == ["a-gemini"]


def test_render_shows_notable_rooms_only() -> None:
    busy = catchup.room_digest("build", HIST, me="arav", since=SINCE)
    quiet = catchup.room_digest("chat", [_m("a-claude", "hi", "2026-10-16T11:00:00Z")],
                                me="arav", since=SINCE)
    text = "\n".join(r.plain for r in catchup.render([busy, quiet]))
    assert "While you were away" in text and "#build" in text and "#chat" not in text
    assert "2 commits shipped to main" in text and "1 needs you" in text and "stuck: @a-gemini" in text
    assert "@a-codex: @arav should the token" in text
    assert catchup.render([quiet]) == []


def test_last_seen_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "last-seen-arav.json"
    assert catchup.load_last_seen(p) is None
    catchup.save_last_seen(p, 123.5)
    assert catchup.load_last_seen(p) == 123.5
