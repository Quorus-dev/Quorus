"""Per-agent persistent room memory — Stream B leaf module.

Goal
----
Each Quorus agent gets a small, durable, append-only summary log of what it
just did, scoped per-room. The reflexd daemon writes a 1-sentence summary
after every successful reply, and loads the most recent ~10 entries into
the next wake-prompt context as a "RECENT MEMORY" section. This closes
one of the five group-chat gaps from the May-4 sprint plan: agents that
forget their own prior actions in the same room.

Storage
-------
``~/.quorus/memory/<participant>/<room>.jsonl``

* Append-only JSONL — one JSON object per line, sorted oldest-first.
* Hard cap: 5KB per file. When exceeded, the *oldest* lines are dropped
  via atomic-replace (write tmp file, ``os.replace``).
* File mode: 0o600 (owner read/write only). Some of these summaries
  reflect agent reasoning over PHI/PII-ish chat content, so we treat
  them as private-by-default.
* Corruption recovery: a truncated last line is silently skipped on
  read; a fully-malformed file is treated as empty (no exception
  bubbled to the daemon).

This is a stdlib-only module — no httpx, no sqlite, no fcntl. All concurrency
safety comes from the atomic-replace pattern; concurrent writers within the
same process serialize on the per-file lock kept here.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Hard cap per file. Oldest entries are dropped to fit. 5KB chosen because
# 10 short summaries (~250B each) + room for ~10 more is enough for one
# wake-prompt context, and the cap also limits prompt-injection blast
# radius if a malicious agent tries to fill the file.
MAX_BYTES = 5 * 1024  # 5KB
DEFAULT_READ_LIMIT = 10
FILE_MODE = 0o600

# Regex for sanitising participant/room components used in path construction.
# Same conservative shape used elsewhere (cursor inbox dir, busy-file path).
_SAFE = re.compile(r"[^A-Za-z0-9._-]")

# Per-process LRU lock cache so concurrent appends from the same daemon
# don't tear the JSONL file. Cross-process safety is handled by the
# atomic-replace pattern (oldest-evict path) — a torn line on read is
# discarded by the corruption-recovery branch.
#
# H9 fix: bound the lock map at _LOCKS_MAX entries. Long-running daemons
# accumulate one lock per (participant, room) forever otherwise. We keep
# the most recently used N locks and evict the LRU entry when full.
_LOCKS_MAX = 1024
_LOCKS_LRU: "OrderedDict[str, asyncio.Lock]" = OrderedDict()


def _safe(name: str, *, fallback: str = "default") -> str:
    """Sanitise a path component — keep alnum + ._- and trim to 64 chars.

    Belt-and-braces against an attacker-controlled participant or room
    name landing in the filesystem path. Mirrors
    ``HeadlessAdapter._write_cursor_inbox``'s sanitiser.
    """
    if not name:
        return fallback
    cleaned = _SAFE.sub("_", name)[:64]
    return cleaned or fallback


def memory_dir(*, base_dir: Path | None = None) -> Path:
    """Root memory dir. Override via *base_dir* for tests."""
    if base_dir is not None:
        return Path(base_dir)
    return Path.home() / ".quorus" / "memory"


def memory_path(
    participant: str,
    room: str,
    *,
    base_dir: Path | None = None,
) -> Path:
    """Return the JSONL path for ``(participant, room)``. Does NOT create."""
    root = memory_dir(base_dir=base_dir)
    return root / _safe(participant) / f"{_safe(room)}.jsonl"


def _ensure_parent(path: Path) -> None:
    """mkdir -p the parent dir, mode 0o700 to match the file's privacy."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Re-tighten if the dir was created previously with a looser umask.
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        # Best-effort — non-POSIX hosts (Windows tests) may not support
        # chmod's full surface. The functional path still works.
        pass


def _lock_for(path: Path) -> asyncio.Lock:
    """Return (creating if needed) an asyncio.Lock for ``path``.

    H9 fix: backed by an LRU dict capped at ``_LOCKS_MAX`` entries. Each
    access promotes the key to the right end; eviction drops the oldest.
    Evicting a lock is safe because any caller currently holding it owns
    the strong reference; new callers will mint a fresh lock — at worst
    serialisation degrades to "no contention" for an evicted (rare) key.
    """
    key = str(path)
    lock = _LOCKS_LRU.get(key)
    if lock is not None:
        _LOCKS_LRU.move_to_end(key)
        return lock
    if len(_LOCKS_LRU) >= _LOCKS_MAX:
        _LOCKS_LRU.popitem(last=False)
    lock = asyncio.Lock()
    _LOCKS_LRU[key] = lock
    return lock


def _atomic_replace(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically, with mode 0o600.

    The tmp file is created in the SAME directory so ``os.replace`` is
    cross-device-safe. Permission is set on the tmp file BEFORE rename so
    there's no observable window of broader permissions.
    """
    _ensure_parent(path)
    fd, tmp = tempfile.mkstemp(prefix=".memory_", suffix=".tmp", dir=path.parent)
    try:
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_lines_safe(path: Path) -> list[dict[str, Any]]:
    """Read JSONL, skipping a torn last line and any malformed earlier ones.

    Returns a list of dicts ordered oldest-first. Missing file ⇒ ``[]``.
    Empty file ⇒ ``[]``. Fully unreadable file ⇒ ``[]`` (we never crash
    the daemon on corrupt memory).
    """
    if not path.exists():
        return []
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Skip malformed/torn lines — never raise into the caller.
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _trim_to_cap(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop oldest entries until the encoded payload fits MAX_BYTES."""
    if not entries:
        return entries
    encoded = "\n".join(
        json.dumps(e, separators=(",", ":")) for e in entries
    ) + "\n"
    if len(encoded.encode("utf-8")) <= MAX_BYTES:
        return entries
    pruned = list(entries)
    while pruned:
        encoded = "\n".join(
            json.dumps(e, separators=(",", ":")) for e in pruned
        ) + "\n"
        if len(encoded.encode("utf-8")) <= MAX_BYTES:
            break
        pruned.pop(0)
    return pruned


async def append(
    participant: str,
    room: str,
    summary: str,
    *,
    ts: str | None = None,
    extra: dict[str, Any] | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Append a 1-sentence summary to the participant's room memory.

    *ts* defaults to the current UTC ISO-8601 stamp. *extra* is merged
    INTO the entry body, with the standard keys (``ts``, ``room``,
    ``summary``) winning on conflict so callers can't shadow them. Returns
    the persisted entry dict.

    The whole operation is async-lock protected per file, so concurrent
    appends from the same daemon don't tear the JSONL. Cross-process
    concurrency is rare (one reflexd per agent host) and handled
    gracefully by the corruption-recovery read path.
    """
    if not summary or not summary.strip():
        # No-op on empty summaries — keeps the memory file clean.
        return {}
    path = memory_path(participant, room, base_dir=base_dir)
    entry: dict[str, Any] = dict(extra or {})
    entry["ts"] = ts or datetime.now(timezone.utc).isoformat()
    entry["room"] = room
    entry["summary"] = summary.strip()[:500]  # hard-cap each line

    async with _lock_for(path):
        existing = _read_lines_safe(path)
        existing.append(entry)
        trimmed = _trim_to_cap(existing)
        encoded = "\n".join(
            json.dumps(e, separators=(",", ":")) for e in trimmed
        ) + "\n"
        await asyncio.to_thread(
            _atomic_replace, path, encoded.encode("utf-8")
        )
    return entry


async def read_recent(
    participant: str,
    room: str,
    n: int = DEFAULT_READ_LIMIT,
    *,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the most recent *n* entries (newest last). Empty list if
    file missing/corrupt. Never raises — the daemon must keep running
    even if memory is broken.
    """
    if n <= 0:
        return []
    path = memory_path(participant, room, base_dir=base_dir)
    async with _lock_for(path):
        entries = await asyncio.to_thread(_read_lines_safe, path)
    if len(entries) <= n:
        return entries
    return entries[-n:]


async def cap_to_5kb(
    participant: str,
    room: str,
    *,
    base_dir: Path | None = None,
) -> int:
    """Force-trim the file to fit MAX_BYTES, returning bytes after trim.

    Used by tests to assert the eviction path; production callers don't
    need this directly because :func:`append` trims on every write.
    """
    path = memory_path(participant, room, base_dir=base_dir)
    async with _lock_for(path):
        existing = _read_lines_safe(path)
        trimmed = _trim_to_cap(existing)
        encoded = "\n".join(
            json.dumps(e, separators=(",", ":")) for e in trimmed
        ) + "\n"
        if not trimmed:
            try:
                path.unlink()
                return 0
            except FileNotFoundError:
                return 0
        await asyncio.to_thread(
            _atomic_replace, path, encoded.encode("utf-8")
        )
        return len(encoded.encode("utf-8"))


def _now_ts() -> str:
    """Tiny indirection so tests can monkeypatch the clock if needed."""
    return datetime.now(timezone.utc).isoformat()


# Sync convenience wrapper — the tests import it for write-and-readback
# patterns that don't want to spin an event loop. Production code should
# stick with the async versions.

def append_sync(
    participant: str,
    room: str,
    summary: str,
    *,
    ts: str | None = None,
    extra: dict[str, Any] | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Sync version of :func:`append` for tests + non-async callers."""
    return asyncio.run(
        append(
            participant, room, summary,
            ts=ts, extra=extra, base_dir=base_dir,
        )
    )


def read_recent_sync(
    participant: str,
    room: str,
    n: int = DEFAULT_READ_LIMIT,
    *,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Sync version of :func:`read_recent`."""
    return asyncio.run(
        read_recent(participant, room, n, base_dir=base_dir)
    )


def memory_size_bytes(
    participant: str,
    room: str,
    *,
    base_dir: Path | None = None,
) -> int:
    """Return the current size of the memory file in bytes (0 if missing)."""
    path = memory_path(participant, room, base_dir=base_dir)
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# GDPR right-to-erasure — purge per-(participant, room) memory.
# ---------------------------------------------------------------------------


def _atomic_unlink(path: Path) -> bool:
    """Erase *path* via a tmpfile-replace + unlink to defeat partial reads.

    A naive ``Path.unlink()`` leaves a tiny window where a concurrent
    reader could observe the original bytes via an open fd. By first
    overwriting the file with a zero-length tmpfile and ``os.replace``-ing
    it into place we guarantee any subsequent read sees an empty file
    even if the unlink loses the race. The tmpfile lands in the SAME
    directory so ``os.replace`` is cross-device safe.

    Returns True if the file was present and removed, False if missing.
    """
    if not path.exists():
        return False
    parent = path.parent
    try:
        fd, tmp = tempfile.mkstemp(prefix=".memory_purge_", suffix=".tmp", dir=parent)
    except OSError:
        # Fall back to direct unlink if mkstemp fails (e.g. dir gone).
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
    try:
        os.close(fd)
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)  # path now points at the empty tmpfile
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        # Best-effort fallback: still try to unlink the original path.
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
    try:
        path.unlink()
    except FileNotFoundError:
        # Race lost — file already gone after the replace. Acceptable.
        pass
    return True


async def purge(
    participant: str,
    room: str | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """GDPR Article 17 erasure — drop a participant's memory file(s).

    * ``room=None`` ⇒ purge ALL room files for *participant* and remove
      the now-empty per-participant directory.
    * ``room=<name>`` ⇒ purge just ``<participant>/<room>.jsonl``.

    Atomic: the data file is replaced with a tmpfile and unlinked under
    the per-file asyncio lock so a concurrent ``append`` can't resurrect
    deleted bytes mid-flight.

    Returns ``{"removed": [<rel-path>, ...], "purged_count": N}`` where
    ``rel-path`` is the participant/room form so callers can audit-log
    without re-deriving paths. Missing files are silently skipped — the
    operation is idempotent (same return on repeat invocation).
    """
    safe_p = _safe(participant)
    root = memory_dir(base_dir=base_dir)
    participant_dir = root / safe_p
    removed: list[str] = []

    async def _purge_one(target: Path, label: str) -> None:
        # Hold the per-file lock so an in-flight append can't write
        # ghost bytes after the unlink. Use to_thread for the disk hit
        # so we don't block the event loop on a slow filesystem.
        lock = _lock_for(target)
        async with lock:
            present = await asyncio.to_thread(_atomic_unlink, target)
        if present:
            removed.append(label)

    if room is not None:
        path = memory_path(participant, room, base_dir=base_dir)
        await _purge_one(path, f"{safe_p}/{_safe(room)}.jsonl")
    else:
        if participant_dir.is_dir():
            for entry in sorted(participant_dir.glob("*.jsonl")):
                # Re-derive the safe room name from the file stem so the
                # lock key matches what append() would compute. The stem
                # is already sanitised, so there's no path-traversal risk.
                rname = entry.stem
                target = participant_dir / f"{_safe(rname)}.jsonl"
                await _purge_one(target, f"{safe_p}/{entry.name}")
            # Drop the now-empty dir. Best-effort; if a non-jsonl file
            # was dropped in there by an operator, rmdir will refuse and
            # we keep going (the .jsonl files are still gone).
            try:
                participant_dir.rmdir()
            except OSError:
                pass

    return {
        "participant": safe_p,
        "room": _safe(room) if room else None,
        "removed": removed,
        "purged_count": len(removed),
    }


def purge_sync(
    participant: str,
    room: str | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Sync convenience wrapper around :func:`purge` for the CLI path."""
    return asyncio.run(purge(participant, room, base_dir=base_dir))


__all__ = [
    "MAX_BYTES",
    "DEFAULT_READ_LIMIT",
    "FILE_MODE",
    "append",
    "append_sync",
    "cap_to_5kb",
    "memory_dir",
    "memory_path",
    "memory_size_bytes",
    "purge",
    "purge_sync",
    "read_recent",
    "read_recent_sync",
]


# Ensure ``time`` import isn't dead code if optimisation strips it later.
_ = time
