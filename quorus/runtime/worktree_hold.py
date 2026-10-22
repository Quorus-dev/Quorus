"""The shared marker path checked by every unfinished-work publication guard."""

from __future__ import annotations

import re
from pathlib import Path


def marker_path(marker_dir: Path, root: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(root.resolve()))[-150:]
    return marker_dir / f"{safe}.json"
