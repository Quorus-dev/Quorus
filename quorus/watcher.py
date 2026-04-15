"""Backward-compat shim for the Quorus watcher.

The implementation has moved to the standalone ``quorus-cli`` package
(``quorus_cli.watcher``). This module re-exports its public API so existing
``from quorus.watcher import Watcher`` imports keep working.
"""

from quorus_cli.watcher import *  # noqa: F401,F403
from quorus_cli.watcher import Watcher

__all__ = ["Watcher"]
