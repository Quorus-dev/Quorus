"""The CLI's relay calls: ``httpx.get/post/request`` over one keep-alive client.

Each function takes the same arguments as its ``httpx`` namesake, so a call site
only changes its module and keeps its own timeout. That timeout gets the loopback
connect cap (``with_connect``), and the shared transport retries a stuck connect
(``quorus.http_settings``). ``main()`` calls ``close()`` when the command ends.

Async code uses ``async_client(url)`` instead: one client per use, so none is shared
across event loops. A per-call ``timeout=`` on it goes through ``with_connect`` too.
"""
from __future__ import annotations

import threading

import httpx

from quorus import http_settings as _http_settings
from quorus.http_settings import with_connect  # noqa: F401  (re-exported)

_DEFAULT_TIMEOUT = httpx.Timeout(5.0)  # what httpx.get/post use when given none
_client: httpx.Client | None = None
_lock = threading.Lock()


def _shared() -> httpx.Client:
    global _client
    with _lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(transport=_http_settings.transport())
        return _client


def request(method: str, url: str, **kwargs) -> httpx.Response:
    kwargs["timeout"] = _http_settings.with_connect(
        url, kwargs.get("timeout", _DEFAULT_TIMEOUT))
    return _shared().request(method, url, **kwargs)


def get(url: str, **kwargs) -> httpx.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    return request("POST", url, **kwargs)


def stream(method: str, url: str, **kwargs):
    """``httpx.stream`` over the shared client; use it as a context manager."""
    kwargs["timeout"] = _http_settings.with_connect(
        url, kwargs.get("timeout", _DEFAULT_TIMEOUT))
    return _shared().stream(method, url, **kwargs)


def async_client(url: str, **kwargs) -> httpx.AsyncClient:
    """A new async client for a relay at *url*: retrying transport, connect capped."""
    kwargs["timeout"] = _http_settings.with_connect(url, kwargs.get("timeout", _DEFAULT_TIMEOUT))
    return httpx.AsyncClient(transport=_http_settings.async_transport(), **kwargs)


def close() -> None:
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None
