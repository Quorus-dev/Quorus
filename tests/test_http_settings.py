"""Connect retries happen below httpcore's retry loop, never after a request is sent.

The real HTTPTransport is kept (replacing it would skip httpcore's retry loop): a real
server on 127.0.0.1 answers, and the pool's network backend is wrapped so its first
connects fail the way a stuck TIME_WAIT port does (Codex on 43434e59).

Release backport (0.7.9): only the transport tests; weekend also wires these
settings into the MCP server, reflexd and the SDK, which 0.7.x does not.
"""
from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import httpx
import pytest

from quorus import http_settings


class _Server:
    def __init__(self, stall: float = 0.0):
        self.requests: list[tuple[str, bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive, so reuse is observable

            def _answer(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append((self.command, self.rfile.read(n) if n else b""))
                if stall:
                    time.sleep(stall)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            do_GET = do_POST = _answer

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


class _Flaky(httpcore.SyncBackend):
    def __init__(self, fail: int, exc=httpcore.ConnectTimeout):
        self.fail, self.exc, self.connects = fail, exc, 0

    def connect_tcp(self, *a, **k):
        self.connects += 1
        if self.connects <= self.fail:
            raise self.exc("simulated stuck connect")
        return super().connect_tcp(*a, **k)


class _AsyncFlaky(httpcore.AnyIOBackend):
    def __init__(self, fail: int, exc=httpcore.ConnectTimeout):
        self.fail, self.exc, self.connects = fail, exc, 0

    async def connect_tcp(self, *a, **k):
        self.connects += 1
        if self.connects <= self.fail:
            raise self.exc("simulated stuck connect")
        return await super().connect_tcp(*a, **k)


def _client(url: str, backend) -> httpx.Client:
    t = http_settings.transport()
    t._pool._network_backend = backend
    return httpx.Client(transport=t, timeout=http_settings.with_connect(url, 10))


@pytest.mark.parametrize("exc", [httpcore.ConnectTimeout, httpcore.ConnectError])
def test_two_stuck_connects_then_one_request(server, exc) -> None:
    flaky = _Flaky(2, exc)
    with _client(server.url, flaky) as c:
        r = c.post(f"{server.url}/rooms/r/messages", content=b'{"m":1}')
    assert r.status_code == 200 and flaky.connects == 3
    assert server.requests == [("POST", b'{"m":1}')]  # the body arrived once


def test_too_many_stuck_connects_raise_todays_error(server) -> None:
    with _client(server.url, _Flaky(10)) as c, pytest.raises(httpx.ConnectTimeout):
        c.get(f"{server.url}/health")
    assert server.requests == []


def test_a_read_timeout_is_never_retried() -> None:
    s = _Server(stall=1.0)
    try:
        flaky = _Flaky(0)
        t = http_settings.transport()
        t._pool._network_backend = flaky
        with httpx.Client(transport=t, timeout=httpx.Timeout(10, read=0.2)) as c:
            with pytest.raises(httpx.ReadTimeout):
                c.post(f"{s.url}/x", content=b"once")
        time.sleep(1.2)
        assert flaky.connects == 1 and s.requests == [("POST", b"once")]
    finally:
        s.close()


def test_one_client_reuses_its_connection(server) -> None:
    flaky = _Flaky(0)
    with _client(server.url, flaky) as c:
        c.get(f"{server.url}/a")
        c.get(f"{server.url}/b")
    assert flaky.connects == 1 and len(server.requests) == 2


def test_async_transport_retries_the_same_way(server) -> None:
    async def go():
        flaky = _AsyncFlaky(2)
        t = http_settings.async_transport()
        t._pool._network_backend = flaky
        async with httpx.AsyncClient(transport=t,
                                     timeout=http_settings.with_connect(server.url, 10)) as c:
            r = await c.post(f"{server.url}/m", content=b"a")
        return r.status_code, flaky.connects

    assert asyncio.run(go()) == (200, 3)
    assert server.requests == [("POST", b"a")]


@pytest.mark.parametrize("url, given, connect, read", [
    ("http://127.0.0.1:8080", 10, 2.0, 10),
    ("http://localhost:8080", httpx.Timeout(30, read=None), 2.0, None),
    ("http://[::1]:8080", 1.0, 1.0, 1.0),          # a tighter cap is kept
    ("http://127.0.0.1:8080", None, 2.0, None),
    ("https://relay.example.com", 10, 10, 10),       # remote: unchanged
])
def test_only_the_loopback_connect_part_changes(url, given, connect, read) -> None:
    t = http_settings.with_connect(url, given)
    base = given if isinstance(given, httpx.Timeout) else httpx.Timeout(given)
    assert t.connect == connect and t.read == read
    assert t.write == base.write and t.pool == base.pool
