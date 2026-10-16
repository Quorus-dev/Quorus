"""Atomic, owner-only writes for local credentials."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, TextIO


def _replace_private(path: Path, write: Callable[[TextIO], object]) -> None:
    """Replace a file using a fresh 0600 inode, never truncating a symlink.

    A fixed .tmp filename can already be public or point outside the config
    directory. mkstemp creates an exclusive private file in the same directory
    so replacement is atomic and existing destination permissions cannot leak.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".quorus-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            write(stream)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_private_text(path: Path, text: str) -> None:
    """Atomically write private text without following a destination symlink."""
    _replace_private(path, lambda stream: stream.write(text))


def write_private_json(path: Path, data: Any, **kwargs: Any) -> None:
    """Stream JSON into a fresh private file, replacing only after success."""
    _replace_private(path, lambda stream: json.dump(data, stream, **kwargs))
