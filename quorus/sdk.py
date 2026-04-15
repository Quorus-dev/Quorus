"""Backward-compat shim for the Quorus SDK.

The implementation has moved to the standalone ``quorus-sdk`` package
(``quorus_sdk.sdk``). This module re-exports its public API so existing
``from quorus.sdk import Room`` imports keep working.
"""

from quorus_sdk.http_agent import ReceiveResult
from quorus_sdk.sdk import *  # noqa: F401,F403
from quorus_sdk.sdk import Room

__all__ = ["Room", "ReceiveResult"]
