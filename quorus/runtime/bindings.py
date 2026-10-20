"""The one resolver for this machine's room -> workspace bindings file.

Every reader and writer in the CLI and the relay goes through :func:`bindings_path`,
and the daemon's ``ROOM_BINDINGS_PATH`` (``scripts/reflexd.py``) is the only other
name for it. ``tests/conftest.py`` points this function at a temp file for every
test, so a module that spelled the path itself would read the developer's real
``~/.quorus/room-bindings.json`` (Codex, review of d4d5f235: ``replay_cmd`` did);
``tests/test_room_bindings_isolated.py`` greps the tree so none can."""
from __future__ import annotations

from pathlib import Path

FILENAME = "room-bindings.json"


def bindings_path() -> Path:
    """``~/.quorus/room-bindings.json`` as of now (``Path.home()`` at call time)."""
    return Path.home() / ".quorus" / FILENAME
