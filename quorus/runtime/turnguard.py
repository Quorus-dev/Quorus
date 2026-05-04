"""TurnGuard — busy-file shared between harnesses and reflexd (PR-C2).

When an agent harness is mid-tool-call (Bash, Edit, Read, etc.) it writes
``~/.quorus/runtime/<participant>.busy``. ``reflexd`` checks the same file
before waking the agent on an @-mention so we never interrupt a tool call.

The file is JSON ``{"started_at", "tool", "expires_at", "pid"}`` with a TTL
(default 5min) so a crashed harness can't lock the agent out forever — the
``is_busy`` reader auto-deletes any stale file past its ``expires_at``.

Single source of truth for the busy-file format. ``scripts/reflexd.py``
imports ``is_busy`` from here so the reader and writers can never drift.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BUSY_FILE_SUFFIX = ".busy"
DEFAULT_TTL_SECONDS = 300  # 5 minutes — long enough for slow tool calls.
_RUNTIME_DIR_NAME = "runtime"
_QUORUS_DIR_NAME = ".quorus"

# Path-traversal guard — keep alphanumerics, dot, underscore, hyphen. Same
# regex shape as quorus/runtime/memory.py::_SAFE so writers cannot escape
# the runtime dir via "../../../etc/passwd"-style participant names.
_SAFE_PARTICIPANT = re.compile(r"[^A-Za-z0-9._-]")
_PARTICIPANT_FALLBACK = "anonymous"
_PARTICIPANT_MAX_LEN = 64


def runtime_dir() -> Path:
    """Return ``~/.quorus/runtime/`` (auto-created with 0700 perms).

    PII guard: 0700 keeps tool names & PIDs out of other users' eyes on
    multi-user hosts. Re-evaluates ``Path.home()`` so test monkeypatches
    of ``$HOME`` are honoured.
    """
    path = Path.home() / _QUORUS_DIR_NAME / _RUNTIME_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    # mkdir's ``mode`` arg is masked by umask; chmod is the safe fix.
    try:
        path.chmod(0o700)
    except OSError:
        # Best-effort on filesystems without POSIX perms (e.g. some CI).
        pass
    return path


def busy_path(participant: str, dir_override: Path | None = None) -> Path:
    """Return the busy-file path for ``participant`` (no I/O).

    Path-traversal guard: characters outside ``[A-Za-z0-9._-]`` are
    replaced with ``_`` so a hostile participant string like
    ``"../../../etc/passwd"`` lands inside the runtime dir, not outside.
    Empty/whitespace-only input falls back to ``anonymous`` rather than
    raising — the caller can still feed a sanitised constant for cases
    like daemon startup. Trailing trim caps the filename at 64 chars.
    """
    base = dir_override if dir_override is not None else runtime_dir()
    stripped = (participant or "").strip()
    if not stripped:
        raise ValueError("participant must be non-empty")
    safe = _SAFE_PARTICIPANT.sub("_", stripped)[:_PARTICIPANT_MAX_LEN]
    if not safe:
        safe = _PARTICIPANT_FALLBACK
    return base / f"{safe}{BUSY_FILE_SUFFIX}"


def _now_ts() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def begin(
    participant: str,
    *,
    tool: str | None = None,
    ttl: int = DEFAULT_TTL_SECONDS,
    dir_override: Path | None = None,
) -> Path:
    """Write the busy-file for ``participant``. Idempotent — extends TTL.

    Body is JSON ``{started_at, tool, expires_at, pid}``. ``tool`` is the
    NAME only — never arguments — to keep PII out. Returns the file path.
    """
    if ttl <= 0:
        raise ValueError("ttl must be positive")
    path = busy_path(participant, dir_override=dir_override)
    now = _now_ts()
    started_at: str
    if path.exists():
        # Re-running begin extends TTL but preserves the original started_at
        # so callers can see how long the agent has been continuously busy.
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            started_at = existing.get("started_at") or _iso(now)
        except (OSError, json.JSONDecodeError, ValueError):
            started_at = _iso(now)
    else:
        started_at = _iso(now)
    payload = {
        "started_at": started_at,
        "tool": tool or "",
        "expires_at": _iso(now + ttl),
        "pid": os.getpid(),
    }
    # M8 fix: write tmp + atomic replace. The previous O_TRUNC path
    # truncated the busy-file BEFORE writing the JSON; if the process
    # was killed mid-write, the file was left empty and ``is_busy`` saw
    # a zero-byte file as "no busy file" — turnguard would let a second
    # tool call slip through during a real busy window.
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".busy_", suffix=".tmp", dir=path.parent)
    try:
        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        # 0o600 set on the tmp file BEFORE rename — never observable as
        # broader perms.
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


def end(participant: str, *, dir_override: Path | None = None) -> None:
    """Delete the busy-file for ``participant``. No-op if absent."""
    path = busy_path(participant, dir_override=dir_override)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        # Permission / filesystem issue — surface, don't swallow silently in
        # production but don't crash the caller's tool call either.
        return


def is_busy(participant: str, dir_override: Path | None = None) -> bool:
    """Return True iff a non-expired busy-file exists.

    TTL semantics:

    - File with parseable ``expires_at`` past now → auto-deleted, returns False.
      (A crashed harness can't permanently block reflexd.)
    - File with parseable ``expires_at`` in the future → True.
    - File without parseable JSON / no ``expires_at`` (legacy / raw text) →
      treated as busy (True). The presence of the file is the signal; we
      don't second-guess a writer that didn't speak our wire format.
    - **Empty** file (zero bytes / whitespace only) → NOT busy (False) +
      debug log. An empty busy-file is almost always a half-written write
      from a crashed harness — blocking on it forever would be the same
      bug TTL was designed to prevent. We treat it as if no file existed.
    - No file → False.
    """
    path = busy_path(participant, dir_override=dir_override)
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        # Can't read but it's there — treat as busy (fail safe: don't wake).
        return True
    if not raw.strip():
        # L1 fix: empty file = crashed-mid-write; don't permanently block.
        logger.debug(
            "turnguard: empty busy-file for %s — treating as not-busy",
            participant,
        )
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = None
    if not isinstance(data, dict):
        return True
    expires_iso = data.get("expires_at")
    if not expires_iso:
        # JSON but no TTL — accept the file's presence as the signal.
        return True
    try:
        expires_at = datetime.fromisoformat(expires_iso)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    if expires_at.timestamp() <= _now_ts():
        end(participant, dir_override=dir_override)
        return False
    return True


@contextlib.contextmanager
def busy(
    participant: str,
    *,
    tool: str | None = None,
    ttl: int = DEFAULT_TTL_SECONDS,
    dir_override: Path | None = None,
):
    """Context manager: ``with busy(p, tool="Bash"): ...``.

    Used by agent-loop harnesses we control (codex/claude/gemini agents)
    to mark the runtime busy for the duration of a subprocess call.
    Always clears the busy-file in ``finally``, even on KeyboardInterrupt
    or subprocess crash, so a tool-call panic doesn't strand the marker.
    Failures to write the busy-file degrade silently — the agent loop
    must keep working even if the runtime dir is unwritable.
    """
    wrote = False
    try:
        try:
            begin(participant, tool=tool, ttl=ttl, dir_override=dir_override)
            wrote = True
        except (OSError, ValueError):
            wrote = False
        yield
    finally:
        if wrote:
            try:
                end(participant, dir_override=dir_override)
            except Exception:  # pragma: no cover — defensive
                pass
