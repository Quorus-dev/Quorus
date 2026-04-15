"""Backward-compat shim for the Quorus HTTP client.

The implementation has moved to the standalone ``quorus-sdk`` package
(``quorus_sdk.http_agent``). This module re-exports its public API so
existing ``from quorus.integrations.http_agent import QuorusClient``
imports keep working.
"""

from quorus_sdk.http_agent import *  # noqa: F401,F403
from quorus_sdk.http_agent import AckError, QuorusClient, ReceiveResult

__all__ = ["AckError", "QuorusClient", "ReceiveResult"]
