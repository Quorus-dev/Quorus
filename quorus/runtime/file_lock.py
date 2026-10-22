"""Exclusive file locks that import on every platform.

POSIX uses ``fcntl.flock``; Windows has no ``fcntl``, so a top-level import there
crashed every ``quorus`` command at startup (PR #20 cold-install, 0.7.0). Windows
locks the file's first byte with ``msvcrt.locking``. Anything else fails closed
with OSError, the same error a held lock raises.
"""
from __future__ import annotations

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]


def lock(fd: int, *, blocking: bool = True) -> None:
    """Take an exclusive lock on *fd*. Raises OSError if it's held (non-blocking)
    or if this platform has no lock primitive at all."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    try:
        import msvcrt
    except ImportError as exc:
        raise OSError("no file lock primitive on this platform") from exc
    # LK_LOCK retries for about 10 s, then raises OSError.
    msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
