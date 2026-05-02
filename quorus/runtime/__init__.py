"""Quorus client-side runtime helpers.

Holds the file formats and helpers that bridge user-facing harnesses
(Claude Code, Codex CLI, Gemini CLI, Cursor) and the local reflex daemon.

All file paths in this package live under ``~/.quorus/runtime/`` (mode 0700)
so they survive harness restarts but never leak across users on a shared
host. Nothing here speaks to the relay — that lane is owned by reflexd.
"""

from quorus.runtime.turnguard import (
    BUSY_FILE_SUFFIX,
    DEFAULT_TTL_SECONDS,
    begin,
    busy,
    busy_path,
    end,
    is_busy,
    runtime_dir,
)

__all__ = [
    "BUSY_FILE_SUFFIX",
    "DEFAULT_TTL_SECONDS",
    "begin",
    "busy",
    "busy_path",
    "end",
    "is_busy",
    "runtime_dir",
]
