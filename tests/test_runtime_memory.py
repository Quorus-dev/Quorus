"""Tests for :mod:`quorus.runtime.memory` — Stream B persistent agent memory.

Coverage targets the 10 cases called out in the Stream B plan:
* basic round-trip (write/read)
* eviction at 5KB cap (oldest first)
* atomic replace under concurrent writes
* corruption recovery (truncated/garbled lines silently dropped)
* missing dir auto-created
* file mode 0o600
* path sanitisation (participant + room with special chars)
* read_recent honors n
* read_recent on empty/missing returns []
* cap_to_5kb returns 0 when nothing remains
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from quorus.runtime import memory as mem


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    """Isolated memory dir per test — never touches the real ~/.quorus."""
    return tmp_path / "memory_root"


# ---------------------------------------------------------------------------
# 1. Basic round-trip
# ---------------------------------------------------------------------------


async def test_append_then_read_roundtrip(base_dir: Path):
    entry = await mem.append(
        "alice", "general", "filed PR-1234", base_dir=base_dir,
    )
    assert entry["summary"] == "filed PR-1234"
    assert entry["room"] == "general"
    assert "ts" in entry

    recent = await mem.read_recent("alice", "general", base_dir=base_dir)
    assert len(recent) == 1
    assert recent[0]["summary"] == "filed PR-1234"


# ---------------------------------------------------------------------------
# 2. Eviction at 5KB cap (oldest first)
# ---------------------------------------------------------------------------


async def test_eviction_drops_oldest_at_5kb(base_dir: Path):
    # Each entry is ~80B serialised. 200 entries ≈ 16KB → must trim to ≤5KB.
    for i in range(200):
        await mem.append(
            "agent-codex", "swarm", f"summary-{i:03d}", base_dir=base_dir,
        )
    size = mem.memory_size_bytes("agent-codex", "swarm", base_dir=base_dir)
    assert size <= mem.MAX_BYTES, (
        f"file should be <= {mem.MAX_BYTES} bytes, got {size}"
    )
    recent = await mem.read_recent(
        "agent-codex", "swarm", n=200, base_dir=base_dir,
    )
    # All retained entries must be a contiguous *tail* of the original
    # write sequence — eviction is oldest-first.
    nums = [int(e["summary"].split("-")[1]) for e in recent]
    assert nums == sorted(nums)
    assert nums[-1] == 199, "newest write must survive"
    assert nums[0] > 0, "oldest write should have been evicted"


# ---------------------------------------------------------------------------
# 3. Atomic replace under concurrent writes
# ---------------------------------------------------------------------------


async def test_atomic_replace_under_concurrent_writes(base_dir: Path):
    async def writer(idx: int):
        await mem.append(
            "agent-claude", "ops", f"task-{idx}", base_dir=base_dir,
        )

    await asyncio.gather(*(writer(i) for i in range(50)))
    # All 50 writes must end up in the file in some order, with no torn
    # JSON lines (corruption recovery would mask torn lines on read; we
    # check the raw file has 50 valid lines).
    path = mem.memory_path("agent-claude", "ops", base_dir=base_dir)
    raw = path.read_bytes().decode("utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) == 50
    for line in lines:
        json.loads(line)  # raises if any line is torn


# ---------------------------------------------------------------------------
# 4. Corruption recovery — truncated last line is silently skipped
# ---------------------------------------------------------------------------


async def test_truncated_last_line_skipped_on_read(base_dir: Path):
    await mem.append(
        "alice", "ops", "valid entry one", base_dir=base_dir,
    )
    await mem.append(
        "alice", "ops", "valid entry two", base_dir=base_dir,
    )

    # Corrupt the file by appending a torn line
    path = mem.memory_path("alice", "ops", base_dir=base_dir)
    with path.open("ab") as f:
        f.write(b'{"ts":"2026-05-02T00:00:00","summa')  # truncated

    # Reader must not raise; should return only the two clean entries.
    recent = await mem.read_recent("alice", "ops", base_dir=base_dir)
    summaries = [e["summary"] for e in recent]
    assert "valid entry one" in summaries
    assert "valid entry two" in summaries
    assert len(recent) == 2


async def test_unparseable_line_in_middle_is_skipped(base_dir: Path):
    path = mem.memory_path("alice", "ops", base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        b'{"ts":"2026-01-01T00:00:00","room":"ops","summary":"a"}\n'
        b'this is not json at all\n'
        b'{"ts":"2026-01-02T00:00:00","room":"ops","summary":"b"}\n'
    )
    path.write_bytes(payload)
    recent = await mem.read_recent("alice", "ops", base_dir=base_dir)
    assert [e["summary"] for e in recent] == ["a", "b"]


# ---------------------------------------------------------------------------
# 5. Missing dir auto-created
# ---------------------------------------------------------------------------


async def test_missing_dir_auto_created(base_dir: Path):
    assert not base_dir.exists()
    await mem.append(
        "fresh-agent", "fresh-room", "first ever", base_dir=base_dir,
    )
    path = mem.memory_path(
        "fresh-agent", "fresh-room", base_dir=base_dir,
    )
    assert path.parent.exists()
    assert path.exists()


# ---------------------------------------------------------------------------
# 6. File mode 0o600
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-only file-mode check",
)
async def test_file_mode_0o600(base_dir: Path):
    await mem.append(
        "alice", "ops", "secret summary", base_dir=base_dir,
    )
    path = mem.memory_path("alice", "ops", base_dir=base_dir)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# 7. Path sanitisation (participant + room with special chars)
# ---------------------------------------------------------------------------


async def test_path_sanitisation(base_dir: Path):
    # Path-traversal-shaped name must not escape the memory dir.
    bad_participant = "../../etc/passwd"
    bad_room = "../../../tmp/evil"
    await mem.append(
        bad_participant, bad_room, "do not escape", base_dir=base_dir,
    )
    # The escaped path stays under base_dir
    path = mem.memory_path(bad_participant, bad_room, base_dir=base_dir)
    assert base_dir in path.parents


# ---------------------------------------------------------------------------
# 8. read_recent honors n
# ---------------------------------------------------------------------------


async def test_read_recent_honors_n(base_dir: Path):
    for i in range(20):
        await mem.append(
            "alice", "ops", f"entry-{i:02d}", base_dir=base_dir,
        )
    recent = await mem.read_recent("alice", "ops", n=5, base_dir=base_dir)
    assert len(recent) == 5
    # newest last
    nums = [int(e["summary"].split("-")[1]) for e in recent]
    assert nums == [15, 16, 17, 18, 19]


# ---------------------------------------------------------------------------
# 9. read_recent on empty/missing returns []
# ---------------------------------------------------------------------------


async def test_read_recent_missing_file(base_dir: Path):
    recent = await mem.read_recent(
        "never-wrote", "anything", base_dir=base_dir,
    )
    assert recent == []


# ---------------------------------------------------------------------------
# 10. cap_to_5kb on missing file returns 0
# ---------------------------------------------------------------------------


async def test_cap_to_5kb_missing_file(base_dir: Path):
    size = await mem.cap_to_5kb(
        "never-wrote", "anything", base_dir=base_dir,
    )
    assert size == 0


async def test_empty_summary_is_noop(base_dir: Path):
    # Empty / whitespace-only summaries should not pollute the log.
    await mem.append("alice", "ops", "   ", base_dir=base_dir)
    recent = await mem.read_recent("alice", "ops", base_dir=base_dir)
    assert recent == []


# ---------------------------------------------------------------------------
# H9 regression — _LOCKS LRU cap evicts oldest at capacity
# ---------------------------------------------------------------------------


async def test_locks_lru_evicts_oldest_at_capacity(monkeypatch, base_dir: Path):
    """H9 — long-running daemon must not accumulate one lock per
    (participant, room) forever. Verify the LRU drops the oldest entry
    when adding past the cap.
    """
    # Shrink the cap so the test runs in O(N) tiny iterations.
    monkeypatch.setattr(mem, "_LOCKS_MAX", 4, raising=True)
    monkeypatch.setattr(
        mem, "_LOCKS_LRU", type(mem._LOCKS_LRU)(), raising=True,
    )

    # Touch 6 distinct (participant, room) pairs — capacity is 4 so the
    # oldest two MUST have been evicted.
    for i in range(6):
        await mem.append(
            f"agent-{i}", "ops", f"line {i}", base_dir=base_dir,
        )

    assert len(mem._LOCKS_LRU) == 4
    keys = [str(mem.memory_path(f"agent-{i}", "ops", base_dir=base_dir))
            for i in range(6)]
    # Oldest two evicted.
    for evicted in keys[:2]:
        assert evicted not in mem._LOCKS_LRU
    # Newest four retained.
    for retained in keys[2:]:
        assert retained in mem._LOCKS_LRU


async def test_locks_lru_promotes_recent_access(monkeypatch, base_dir: Path):
    """Accessing an existing entry promotes it to the recent end."""
    monkeypatch.setattr(mem, "_LOCKS_MAX", 3, raising=True)
    monkeypatch.setattr(
        mem, "_LOCKS_LRU", type(mem._LOCKS_LRU)(), raising=True,
    )

    # Seed three entries.
    for name in ("a", "b", "c"):
        await mem.append(name, "ops", "x", base_dir=base_dir)

    # Re-touch "a" so it becomes the most-recent.
    await mem.append("a", "ops", "again", base_dir=base_dir)

    # Add a fourth — "b" (now LRU) should evict, not "a".
    await mem.append("d", "ops", "y", base_dir=base_dir)

    keys = list(mem._LOCKS_LRU.keys())
    assert any("/a/" in k for k in keys), "promoted entry was evicted"
    assert not any("/b/" in k for k in keys), "stale entry not evicted"


# ---------------------------------------------------------------------------
# M3 — GDPR right-to-erasure (purge)
# ---------------------------------------------------------------------------


async def test_memory_purge_deletes_room_file(base_dir: Path):
    """M3 — purge(participant, room=R) atomically erases just R's file.

    Other rooms for the same participant must survive; the file-gone
    counter is reflected in the return dict so the audit trail can log
    the exact erasure.
    """
    await mem.append("alice", "sprint", "did the thing", base_dir=base_dir)
    await mem.append("alice", "demo", "another thing", base_dir=base_dir)

    target = mem.memory_path("alice", "sprint", base_dir=base_dir)
    other = mem.memory_path("alice", "demo", base_dir=base_dir)
    assert target.exists()
    assert other.exists()

    result = await mem.purge("alice", "sprint", base_dir=base_dir)
    assert result["purged_count"] == 1
    assert any("sprint" in p for p in result["removed"])

    # The targeted room file is gone; the other room survives.
    assert not target.exists()
    assert other.exists()


async def test_memory_purge_all_clears_participant_dir(base_dir: Path):
    """M3 — purge(participant, room=None) erases every room file and the dir."""
    for room in ("sprint", "demo", "build"):
        await mem.append("alice", room, f"note-{room}", base_dir=base_dir)

    participant_dir = mem.memory_dir(base_dir=base_dir) / "alice"
    assert participant_dir.is_dir()

    result = await mem.purge("alice", base_dir=base_dir)
    assert result["purged_count"] == 3
    assert not participant_dir.exists()


async def test_memory_purge_idempotent_on_missing(base_dir: Path):
    """M3 — purging a participant who has no memory returns purged_count=0."""
    result = await mem.purge("ghost", base_dir=base_dir)
    assert result["purged_count"] == 0
    assert result["removed"] == []


async def test_memory_purge_atomic_replace_no_partial_read(base_dir: Path):
    """M3 — after purge() returns, a stat() must show the file gone.

    Also asserts no leftover .tmp files in the parent dir — the
    atomic-replace path must clean up after itself even on the happy
    path.
    """
    await mem.append("alice", "sprint", "x", base_dir=base_dir)
    path = mem.memory_path("alice", "sprint", base_dir=base_dir)
    assert path.exists()
    await mem.purge("alice", "sprint", base_dir=base_dir)
    assert not path.exists()
    leftovers = (
        list(path.parent.glob(".memory_purge_*.tmp"))
        if path.parent.exists() else []
    )
    assert not leftovers, f"tmpfile leftover after purge: {leftovers}"


def test_memory_purge_sync_wrapper_round_trips(base_dir: Path):
    """M3 — purge_sync() mirrors purge() for the CLI path."""
    mem.append_sync("alice", "sprint", "via cli", base_dir=base_dir)
    path = mem.memory_path("alice", "sprint", base_dir=base_dir)
    assert path.exists()

    result = mem.purge_sync("alice", "sprint", base_dir=base_dir)
    assert result["purged_count"] == 1
    assert not path.exists()


# ---------------------------------------------------------------------------
# L30 — UTF-8-safe truncation at the 500-byte boundary
# ---------------------------------------------------------------------------


def test_truncate_utf8_below_cap_passthrough():
    """ASCII strings under cap return unchanged."""
    assert mem._truncate_utf8("hello", 500) == "hello"
    assert mem._truncate_utf8("", 500) == ""


def test_truncate_utf8_ascii_at_boundary():
    """Pure-ASCII string longer than cap truncates exactly to cap bytes."""
    long_ascii = "x" * 800
    out = mem._truncate_utf8(long_ascii, 500)
    assert len(out) == 500
    assert len(out.encode("utf-8")) == 500


def test_truncate_utf8_never_emits_invalid_bytes():
    """Multi-byte chars at the boundary must NOT produce torn UTF-8.

    Each ``é`` is 2 bytes in UTF-8. A pure ``[:n]`` byte slice at an odd
    boundary would leave a half-character; the helper must clip to a
    valid code-point boundary instead.
    """
    payload = "é" * 400  # 800 bytes
    out = mem._truncate_utf8(payload, 501)
    encoded = out.encode("utf-8")
    # Must fit under cap and decode as valid UTF-8.
    assert len(encoded) <= 501
    # Round-trip is lossless — no replacement chars introduced.
    assert all(c == "é" for c in out)
    # 501 byte budget / 2 bytes per char = 250 full chars, dropping the
    # half-char at the boundary.
    assert len(out) == 250


def test_truncate_utf8_emoji_boundary():
    """4-byte UTF-8 emojis must clip cleanly, never half-emoji."""
    payload = "🎉" * 200  # each = 4 bytes -> 800 bytes
    out = mem._truncate_utf8(payload, 500)
    assert len(out.encode("utf-8")) <= 500
    # 500 / 4 = 125 full emojis; everything remaining is decodable.
    assert all(c == "🎉" for c in out)


async def test_memory_summary_truncation_at_utf8_boundary(base_dir: Path):
    """End-to-end: appended summaries are clipped at 500 bytes, not chars,
    and the on-disk JSONL line is always valid UTF-8."""
    # 600 emojis -> 2400 raw bytes; must clip to <=500 bytes.
    summary = "🎉" * 600
    entry = await mem.append("alice", "ops", summary, base_dir=base_dir)
    assert "summary" in entry
    assert len(entry["summary"].encode("utf-8")) <= 500
    # The persisted file must be readable JSONL (no torn UTF-8).
    path = mem.memory_path("alice", "ops", base_dir=base_dir)
    raw = path.read_bytes()
    # Decoding fails loudly if we left a half-emoji in the file.
    decoded = raw.decode("utf-8")
    line = decoded.splitlines()[0]
    parsed = json.loads(line)
    assert parsed["summary"] == entry["summary"]


# ---------------------------------------------------------------------------
# M3 regression — LRU lock-eviction race must NOT torn-write JSONL
# ---------------------------------------------------------------------------


async def test_memory_concurrent_purge_and_append_no_torn_writes(
    base_dir: Path, monkeypatch: pytest.MonkeyPatch,
):
    """While coroutine A holds a path's lock and B is waiting on it, the
    LRU must not evict that lock. Eviction would let coroutine C mint a
    fresh lock for the same path, bypassing serialisation and producing
    interleaved (torn) JSONL lines.
    """
    # Shrink the LRU cap so we can force eviction with one extra entry.
    monkeypatch.setattr(mem, "_LOCKS_MAX", 1)
    # Reset LRU state for an isolated test.
    mem._LOCKS_LRU.clear()

    # Pre-warm with a "busy" lock for path P1 — acquire it and hold.
    p1 = mem.memory_path("alice", "busy", base_dir=base_dir)
    busy_lock = mem._lock_for(p1)
    await busy_lock.acquire()
    try:
        # Simulate a waiter on P1 by scheduling a task that tries to acquire
        # the same lock — this puts an entry into ``_waiters``.
        async def _waiter():
            async with busy_lock:
                pass
        waiter_task = asyncio.create_task(_waiter())
        # Yield once so the waiter is registered.
        await asyncio.sleep(0)
        assert busy_lock.locked()
        # Now demand a lock for a DIFFERENT path — this triggers eviction.
        p2 = mem.memory_path("alice", "other", base_dir=base_dir)
        _other_lock = mem._lock_for(p2)
        # The busy lock for P1 must STILL be tracked — eviction must skip
        # it because it's locked + has waiters.
        assert str(p1) in mem._LOCKS_LRU
        # And requesting the same path again must return the SAME lock,
        # not a fresh one.
        same_lock = mem._lock_for(p1)
        assert same_lock is busy_lock
    finally:
        busy_lock.release()
        await waiter_task
