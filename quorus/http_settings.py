"""HTTP settings every Quorus client shares: survive a stuck connect.

After about 49.7 days of macOS uptime the kernel stops reaping TIME_WAIT, and roughly 1
in 5 new loopback connects hang until the client's timeout (team relay, 2026-10-22).
Two settings help without changing what a request does:

- ``transport()``: httpcore retries ``ConnectError`` and ``ConnectTimeout`` inside
  connection setup only, so a request that reached the server is never sent twice.
- ``with_connect(url, t)``: keeps a call's read, write and pool limits and caps only the
  connect wait, at ``LOOPBACK_CONNECT_S`` for a relay on this machine.

The sdk package ships standalone (no ``quorus`` import) and carries a copy of this
module; tests/test_http_settings.py keeps the copy identical. (Not named http.py, which
could shadow the stdlib package.)
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx

RETRIES = 3
LOOPBACK_CONNECT_S = 2.0
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in _LOOPBACK


def transport() -> httpx.HTTPTransport:
    return httpx.HTTPTransport(retries=RETRIES)


def async_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(retries=RETRIES)


def with_connect(url: str, timeout: float | None | httpx.Timeout) -> httpx.Timeout:
    """*timeout* as given, with only its connect part capped for a loopback relay."""
    t = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout)
    if not is_loopback(url):
        return t
    connect = LOOPBACK_CONNECT_S if t.connect is None else min(t.connect, LOOPBACK_CONNECT_S)
    return httpx.Timeout(connect=connect, read=t.read, write=t.write, pool=t.pool)
