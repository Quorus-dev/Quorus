"""Backward-compat shim for the Murmur watcher.

The implementation has moved to the standalone ``murmur-cli`` package
(``murmur_cli.watcher``). This module re-exports its public API so existing
``from murmur.watcher import Watcher`` imports keep working.
"""

from murmur_cli.watcher import *  # noqa: F401,F403
from murmur_cli.watcher import Watcher

__all__ = ["Watcher"]
