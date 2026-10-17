"""`quorus phone`: rooms on your phone, safe by default (2026-10-17)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from quorus_cli import phone_cmd

from quorus import bundle_client


def test_link_carries_the_token_in_the_fragment_only() -> None:
    url = phone_cmd.phone_url("100.64.0.5", 8790, "s3/cr+t", "arav")
    assert url.startswith("http://100.64.0.5:8790/#token=")
    assert "?" not in url and "s3%2Fcr%2Bt" in url and url.endswith("&name=arav")


def test_refuses_to_listen_everywhere() -> None:
    with pytest.raises(ValueError):
        asyncio.run(phone_cmd.forward("0.0.0.0", 0, "127.0.0.1", 1))


def test_forwarder_relays_bytes_both_ways() -> None:
    async def scenario() -> bytes:
        async def echo(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            w.write(b"pong:" + await r.read(100))
            await w.drain()
            w.close()

        target = await asyncio.start_server(echo, "127.0.0.1", 0)
        tport = target.sockets[0].getsockname()[1]
        fwd = await phone_cmd.forward("127.0.0.1", 0, "127.0.0.1", tport)
        fport = fwd.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", fport)
        w.write(b"ping")
        await w.drain()
        data = await asyncio.wait_for(r.read(100), 5)
        w.close()
        fwd.close()
        target.close()
        return data

    assert asyncio.run(scenario()) == b"pong:ping"


def test_without_tailscale_it_says_how_to_share_safely(monkeypatch) -> None:
    monkeypatch.setattr(phone_cmd, "load_config", lambda: {
        "instance_name": "arav", "relay_url": "http://127.0.0.1:8787", "relay_secret": "s"})
    monkeypatch.setattr(phone_cmd, "tailscale_ip", lambda: None)
    monkeypatch.setattr(bundle_client, "bearer", lambda *a, **k: "h.p.s")

    class _Args:
        lan, port = False, None

    with pytest.raises(SystemExit, match="Tailscale"):
        phone_cmd.cmd_phone(_Args(), _Console())


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *a: Any, **_k: Any) -> None:
        self.lines.append(" ".join(map(str, a)))


def _remote(monkeypatch, bearer) -> None:
    monkeypatch.setattr(phone_cmd, "load_config", lambda: {
        "instance_name": "arav", "relay_url": "https://relay.example.com",
        "relay_secret": "admin-secret"})
    monkeypatch.setattr(bundle_client, "bearer", bearer)


class _Args:
    lan, port, with_secret = False, None, False


def test_link_signs_in_as_you_never_with_the_admin_secret(monkeypatch) -> None:
    """The board's Approve/Deny need the viewer's own JWT, and a link holding the
    shared secret let anyone act as anyone (overseer, 2026-10-17)."""
    calls: list[dict] = []

    def bearer(relay_url, **kw):
        calls.append({"relay_url": relay_url, **kw})
        return "h.p.s"

    _remote(monkeypatch, bearer)
    c = _Console()
    phone_cmd.cmd_phone(_Args(), c)
    assert any(line.startswith("https://relay.example.com/#token=h.p.s&name=arav")
               for line in c.lines)
    assert not any("admin-secret" in line for line in c.lines)
    assert calls == [{"relay_url": "https://relay.example.com", "secret": "admin-secret",
                      "name": "arav"}]


def test_old_relay_fails_closed_unless_asked_for_the_secret(monkeypatch) -> None:
    def bearer(*_a, **_k):
        raise bundle_client.BundleClientError("claiming your name failed (404)")

    _remote(monkeypatch, bearer)
    with pytest.raises(SystemExit, match="--with-secret"):
        phone_cmd.cmd_phone(_Args(), _Console())

    class _WithSecret(_Args):
        with_secret = True

    c = _Console()
    phone_cmd.cmd_phone(_WithSecret(), c)
    assert any("#token=admin-secret" in line for line in c.lines)
