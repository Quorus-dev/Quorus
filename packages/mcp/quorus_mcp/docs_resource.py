"""``quorus://llms.txt``: the agent guide, readable over MCP.

The file explains how an agent installs Quorus, joins a room over MCP and uses
the tools. It ships inside the wheel as ``quorus/llms.txt`` (pyproject
force-include) and lives at the repo root for editable installs.

``server.py`` calls :func:`register` on its ``mcp`` instance. A function, not
an import-time decorator, so ``importlib.reload(quorus_mcp.server)`` (the
auth-fallback tests do this) registers the resource on the new instance too.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.fastmcp import FastMCP

URI = "quorus://llms.txt"
_MISSING = "llms.txt is missing from this install; read it at https://quorus.dev/llms.txt"


def llms_txt_path() -> Path | None:
    """The wheel's copy first, then the repo root (editable / worktree installs)."""
    import quorus

    for candidate in (
        Path(quorus.__file__).resolve().parent / "llms.txt",
        Path(__file__).resolve().parents[3] / "llms.txt",
    ):
        if candidate.is_file():
            return candidate
    return None


def llms_txt() -> str:
    path = llms_txt_path()
    return path.read_text(encoding="utf-8") if path else _MISSING


def register(mcp: FastMCP) -> None:
    """Expose llms.txt on ``mcp`` as a text/plain resource."""
    mcp.resource(
        URI,
        name="llms.txt",
        description="How an agent installs Quorus, joins a room over MCP, and uses the tools.",
        mime_type="text/plain",
    )(llms_txt)
