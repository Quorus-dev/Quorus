"""Backward-compat shim for the Murmur SDK.

The implementation has moved to the standalone ``murmur-sdk`` package
(``murmur_sdk.sdk``). This module re-exports its public API so existing
``from murmur.sdk import Room`` imports keep working.
"""

from murmur_sdk.http_agent import ReceiveResult
from murmur_sdk.sdk import *  # noqa: F401,F403
from murmur_sdk.sdk import Room

__all__ = ["Room", "ReceiveResult"]
