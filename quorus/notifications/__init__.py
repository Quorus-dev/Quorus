"""OS-level notification dispatch for Quorus.

The :mod:`quorus.notifications.native` module is the single source of truth
for surfacing chat events on the host's notification center (macOS banner /
``notify-send`` on Linux). On unsupported platforms it logs and returns False
so callers can branch on the boolean return.
"""

from quorus.notifications.native import notify

__all__ = ["notify"]
