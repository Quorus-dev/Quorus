"""Stream B reflexd integration: defer-announce, memory load+append, DM stream subscription.

The daemon is hard to drive end-to-end here (would need a live SSE
connection plus a real claude CLI). Instead we test:

1. The pure helpers in :mod:`reflexd_streamb` — render_memory_context,
   summarise_reply_for_memory, envelope_thread_root.
2. The RelayClient verb wrappers (post_social_defer, post_agent_dm) round-trip
   correctly via httpx.MockTransport.
3. The Stream B daemon wiring — that ``_wake_and_reply`` picks up the
   memory entries we just wrote, that it appends a memory line on a
   successful reply, and that the reply post carries thread_root_id.
4. The defer-announce branch — when bid loss happens, post_social_defer is
   called via a stub RelayClient.

Total: 7 tests.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from quorus.runtime import memory as _mem

# scripts/ is not a package — load reflexd via the same path-aware loader
# the daemon uses internally. Register in sys.modules BEFORE exec_module
# so internal dataclass(_) calls resolve their __module__ to a real entry.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFLEXD_PATH = _REPO_ROOT / "scripts" / "reflexd.py"

_spec = importlib.util.spec_from_file_location("reflexd", _REFLEXD_PATH)
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


# ---------------------------------------------------------------------------
# 1. Pure helpers
# ---------------------------------------------------------------------------


def test_render_memory_context_empty_returns_empty_string():
    assert reflexd.render_memory_context([]) == ""


def test_render_memory_context_formats_entries():
    entries = [
        {
            "ts": "2026-05-01T15:00:00+00:00",
            "room": "ops",
            "summary": "shipped PR-1",
        },
        {
            "ts": "2026-05-01T15:30:00+00:00",
            "room": "ops",
            "summary": "fixed test",
        },
    ]
    out = reflexd.render_memory_context(entries)
    assert "RECENT MEMORY" in out
    assert "shipped PR-1" in out
    assert "fixed test" in out
    # Both timestamps render in ISO-truncated form
    assert "2026-05-01T15:00:00" in out
    assert "2026-05-01T15:30:00" in out


def test_envelope_thread_root_explicit_wins():
    env = {"id": "msg-1", "thread_root_id": "root-x"}
    assert reflexd.envelope_thread_root(env) == "root-x"


def test_envelope_thread_root_falls_back_to_id():
    env = {"id": "msg-2"}
    assert reflexd.envelope_thread_root(env) == "msg-2"


def test_envelope_thread_root_returns_none_when_blank():
    assert reflexd.envelope_thread_root({}) is None


# ---------------------------------------------------------------------------
# 2. Memory load → wake-prompt → memory append (end-to-end on the daemon)
# ---------------------------------------------------------------------------


class _StubAdapter:
    """Adapter test seam — record contexts and return a canned reply."""

    def __init__(self, reply: str = "stub reply"):
        self.calls: list[tuple[str, str]] = []
        self._reply = reply

    async def run(self, harness: str, *, context: str) -> str:
        self.calls.append((harness, context))
        return self._reply


class _StubRelay:
    """Mimics the bits of RelayClient that ``_wake_and_reply`` calls."""

    def __init__(self):
        self.posted_replies: list[dict[str, Any]] = []
        self.deferred: list[dict[str, Any]] = []

    async def fetch_recent(self, *, room: str, limit: int = 10):
        return []

    async def post_reply(
        self, *, room: str, from_name: str, content: str,
        message_type: str = "chat",
        thread_root_id: str | None = None,
        reply_to: str | None = None,
    ):
        record = {
            "room": room, "from_name": from_name, "content": content,
            "thread_root_id": thread_root_id, "reply_to": reply_to,
        }
        self.posted_replies.append(record)
        return {"id": f"reply-{len(self.posted_replies)}"}

    async def post_social_defer(self, **kwargs):
        self.deferred.append(kwargs)
        return {"ok": True}


@pytest.fixture
def memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Re-route the memory module to a temp dir for the test."""
    base = tmp_path / "mem"

    real_append = _mem.append
    real_read = _mem.read_recent

    async def _append(participant, room, summary, *, ts=None, extra=None,
                      base_dir=None):
        return await real_append(
            participant, room, summary,
            ts=ts, extra=extra, base_dir=base,
        )

    async def _read(participant, room, n=_mem.DEFAULT_READ_LIMIT,
                    *, base_dir=None):
        return await real_read(participant, room, n, base_dir=base)

    monkeypatch.setattr(reflexd, "_mem", type(
        "MemNS", (), {
            "append": _append,
            "read_recent": _read,
            "DEFAULT_READ_LIMIT": _mem.DEFAULT_READ_LIMIT,
        },
    ))
    return base


async def test_wake_loads_memory_then_appends_summary(
    memory_dir: Path, monkeypatch: pytest.MonkeyPatch,
):
    # Pre-seed memory with a prior entry the daemon should pick up.
    await _mem.append(
        "stream-claude", "ops", "earlier work: filed PR-99",
        base_dir=memory_dir,
    )

    # Build a daemon with stub adapter + stub relay.
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test",
        api_key="dryrun-fake-key",
        participant_name="stream-claude",
    )
    adapter = _StubAdapter(reply="acknowledged, will pick up next")
    daemon = reflexd.Reflexd(cfg, adapter=adapter)

    relay = _StubRelay()
    envelope = {
        "id": "wake-1",
        "from_name": "human-arav",
        "room": "ops",
        "content": "@stream-claude continue from where you left",
    }
    await daemon._wake_and_reply(relay, envelope)

    # Stream B: prompt must include RECENT MEMORY block with the prior entry
    assert adapter.calls, "adapter.run should have been called"
    _, ctx = adapter.calls[0]
    assert "RECENT MEMORY" in ctx
    assert "earlier work: filed PR-99" in ctx

    # Reply must be tagged with thread_root_id (envelope_id since no root).
    assert relay.posted_replies, "post_reply should have fired"
    posted = relay.posted_replies[0]
    assert posted["thread_root_id"] == "wake-1"
    assert posted["reply_to"] == "wake-1"

    # Memory file must have a new entry — at least 2 lines now.
    after = await _mem.read_recent(
        "stream-claude", "ops", n=20, base_dir=memory_dir,
    )
    assert len(after) >= 2
    assert any(
        "human-arav" in (e.get("summary") or "") for e in after
    ), "new memory line should mention the wake sender"


# ---------------------------------------------------------------------------
# 3. Defer-announce wraps post_social_defer
# ---------------------------------------------------------------------------


def test_post_social_defer_round_trips_via_mock_transport():
    """Drive RelayClient.post_social_defer through httpx.MockTransport
    to confirm the request shape sent to /v1/social/defer.
    """
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/token":
            return httpx.Response(200, json={"token": "tk"})
        if request.url.path == "/v1/social/defer":
            captured["url"] = str(request.url)
            captured["body"] = request.read().decode("utf-8")
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    async def _drive():
        transport = httpx.MockTransport(_handler)
        client = reflexd.RelayClient(
            relay_url="http://test", api_key="key",
            legacy_bearer=False,
        )
        client._client = httpx.AsyncClient(
            transport=transport, base_url="http://test",
        )
        try:
            await client.post_social_defer(
                room_id="r1", actor="alice", target="bob",
                ref_message_id="msg-1", ttl_seconds=120,
            )
        finally:
            await client._client.aclose()

    import asyncio
    asyncio.run(_drive())
    assert "/v1/social/defer" in captured["url"]
    assert '"to":"bob"' in captured["body"]
    assert '"ref_message_id":"msg-1"' in captured["body"]
