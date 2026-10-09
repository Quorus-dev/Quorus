"""Quorus MCP server — exposes messaging tools to MCP clients.

``mcp`` is loaded lazily: the server resolves (and fail-fast validates) its
relay credentials at import time, so an eager import here made a plain
``import quorus_mcp`` exit on any machine not configured yet — which broke
setup.sh's install check on every fresh CI runner.
"""

from typing import Any

__all__ = ["mcp"]


_SUBMODULES = {"channel", "phase1_registry", "phase1_tools", "runtime", "server",
               "sse", "tools"}


def __getattr__(name: str) -> Any:
    if name == "mcp":
        from quorus_mcp.server import mcp

        return mcp
    if name in _SUBMODULES:
        # Resolve submodules on attribute access too: with a lazy package a
        # reloaded/fresh ``quorus_mcp`` object can lack the attribute even
        # though ``quorus_mcp.tools`` is loaded, which broke
        # ``patch("quorus_mcp.tools.x")`` on Python 3.10.
        import importlib

        return importlib.import_module(f"quorus_mcp.{name}")
    raise AttributeError(f"module 'quorus_mcp' has no attribute {name!r}")
