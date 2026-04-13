"""Backward-compat shim for the Murmur HTTP client.

The implementation has moved to the standalone ``murmur-sdk`` package
(``murmur_sdk.http_agent``). This module re-exports its public API so
existing ``from murmur.integrations.http_agent import MurmurClient``
imports keep working.
"""

from murmur_sdk.http_agent import *  # noqa: F401,F403
from murmur_sdk.http_agent import AckError, MurmurClient, ReceiveResult

__all__ = ["AckError", "MurmurClient", "ReceiveResult"]
