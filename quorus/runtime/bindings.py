"""The one resolver for this machine's room -> workspace bindings file.

Every reader and writer in the CLI and the relay goes through :func:`bindings_path`,
and the daemon's ``ROOM_BINDINGS_PATH`` (``scripts/reflexd.py``) is the only other
name for it. ``tests/conftest.py`` points this function at a temp file for every
test, so a module that spelled the path itself would read the developer's real
``~/.quorus/room-bindings.json`` (Codex, review of d4d5f235: ``replay_cmd`` did);
``tests/test_room_bindings_isolated.py`` greps the tree so none can."""
from __future__ import annotations

import os
from pathlib import Path

FILENAME = "room-bindings.json"


def config_dir() -> Path:
    """This daemon's or CLI's own state dir: ``$QUORUS_CONFIG_DIR`` when set, else
    ``~/.quorus`` (``Path.home()`` at call time).

    Two daemons with different config dirs share no bindings, runtime journals or
    hold markers (dogfood plan §2), without a separate HOME, which would also move
    the harness CLIs' own sign-in (Claude on 2c-3). Same override as
    ``quorus.config.resolve_config_dir``, minus its legacy-directory fallback: that
    would silently move existing bindings to an old ~/.murmur or ~/.mcp_tunnel."""
    override = os.environ.get("QUORUS_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".quorus"


def bindings_path() -> Path:
    """``<config dir>/room-bindings.json`` as of now (see :func:`config_dir`)."""
    return config_dir() / FILENAME
