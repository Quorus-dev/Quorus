"""Monorepo sys.path setup so pytest can import the packaged modules
(``quorus_cli``, ``quorus_mcp``, ``quorus_sdk``, ``quorus_tui``) without
requiring an editable install of every package first."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for pkg in ("sdk", "cli", "mcp", "tui"):
    candidate = _ROOT / "packages" / pkg
    if candidate.exists():
        sys.path.insert(0, str(candidate))
