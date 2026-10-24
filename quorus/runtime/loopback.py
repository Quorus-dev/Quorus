"""Bind a local-only relay on both loopbacks, so `localhost` reaches only this relay.

`localhost` resolves to 127.0.0.1 or ::1, and the owner's trust note records it only
when this relay holds both (relay.own_relay_urls). A relay bound to 127.0.0.1 alone
left every first-run profile, which points at `http://localhost:8080`, looking like
a teammate's relay: `quorus agent add` was refused for the relay's own owner
(stranger-path test of 0.7.1, 2026-10-24). If ::1 can't be bound (IPv6 off, or
another process holds the port there), the relay binds 127.0.0.1 only, as before,
and `localhost` stays untrusted rather than possibly reaching that other process.
"""
from __future__ import annotations

import socket


def _bind(family: int, host: str, port: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((host, port))
        sock.set_inheritable(True)
    except BaseException:
        sock.close()
        raise
    return sock


def loopback_sockets(port: int) -> list[socket.socket]:
    """127.0.0.1:*port*, plus ::1 on the same port when that bind succeeds.
    A taken 127.0.0.1 port raises, as uvicorn's own bind would."""
    v4 = _bind(socket.AF_INET, "127.0.0.1", port)
    if not socket.has_ipv6:
        return [v4]
    try:
        return [v4, _bind(socket.AF_INET6, "::1", v4.getsockname()[1])]
    except OSError:
        return [v4]
