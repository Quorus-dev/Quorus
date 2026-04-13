"""Backward-compat shim for the Murmur MCP server.

The implementation has moved to the standalone ``murmur-mcp`` package
(``murmur_mcp.server``). This module aliases itself to ``murmur_mcp.server``
so existing ``from murmur.mcp_server import ...`` imports keep working,
including tests that ``patch.object(mcp_server, "RELAY_URL", ...)`` — both
names now resolve to the same module object.

The entrypoint ``python -m murmur.mcp_server`` continues to work because the
``__main__`` guard below re-invokes ``mcp.run(transport="stdio")``.
"""

import sys as _sys

from murmur_mcp import server as _server

# Preserve the ability to run as ``python -m murmur.mcp_server`` or as a
# direct script (``python murmur/mcp_server.py``). The check must happen
# BEFORE we alias this module, otherwise ``__name__`` may be lost.
if __name__ == "__main__":
    _server.mcp.run(transport="stdio")
else:
    # Make ``murmur.mcp_server`` and ``murmur_mcp.server`` the same module
    # object so attribute mutations (e.g. monkeypatching module-level
    # constants like ``RELAY_URL``) are observed by the functions defined in
    # ``murmur_mcp.server``.
    _sys.modules[__name__] = _server
