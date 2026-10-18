"""`quorus phone`: your rooms on your phone (2026-10-17).

Arav spent a weekend away from the laptop with no way to watch or steer his
agent rooms. The relay serves a phone-ready page at `/`, but it only listens on
127.0.0.1. This command shares it while it runs, safe by default:

* only on your Tailscale network (just your own devices) unless you pass `--lan`;
* never on every interface (0.0.0.0);
* the link puts the credential in the URL fragment, which browsers never send
  to servers, logs or Referer headers;
* on a shared-secret relay the credential is your own participant JWT (it
  expires, and the board's Approve/Deny need it), never the admin secret;
* sharing stops when you press Ctrl-C.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
from typing import Any
from urllib.parse import quote, urlparse

from quorus.config import load_config

DEFAULT_PORT = 8790


def tailscale_ip() -> str | None:
    exe = shutil.which("tailscale")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    ip = (out.stdout.strip().splitlines() or [""])[0].strip()
    return ip if out.returncode == 0 and ip.startswith("100.") else None


def lan_ip() -> str | None:
    """This machine's address on the local network (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: routing lookup only
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


def phone_url(host: str, port: int, token: str, name: str) -> str:
    # Fragment, never query: fragments don't reach servers, logs or Referer.
    return f"http://{host}:{port}/#token={quote(token, safe='')}&name={quote(name, safe='')}"


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def forward(listen_host: str, listen_port: int, target_host: str,
                  target_port: int) -> asyncio.base_events.Server:
    """A plain TCP forwarder from the phone-reachable address to the local relay."""
    if listen_host in ("0.0.0.0", "::", ""):
        raise ValueError("refusing to listen on every interface")

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            tr, tw = await asyncio.open_connection(target_host, target_port)
        except OSError:
            w.close()
            return
        await asyncio.gather(_pipe(r, tw), _pipe(tr, w))

    return await asyncio.start_server(handle, listen_host, listen_port)


def _print_qr(console: Any, url: str) -> bool:
    try:
        import qrcode  # optional: pip install qrcode
    except ImportError:
        return False
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)
    return True


def _participant_token(cfg: dict, me: str, *, with_secret: bool) -> str:
    """The phone link's credential on a shared-secret relay: a JWT for *me* only.

    The admin secret in a link let anyone holding it read every room and act as
    anyone, and the board showed no Approve/Deny for it (overseer, 2026-10-17).
    """
    if with_secret:
        return cfg["relay_secret"]
    from quorus.bundle_client import BundleClientError, bearer

    try:
        return bearer(cfg["relay_url"], secret=cfg["relay_secret"], name=me)
    except BundleClientError as exc:
        raise SystemExit(
            f"phone: couldn't sign in as {me} ({exc}). Update the relay to a build with "
            "participant keys, or pass --with-secret to put the admin secret in the "
            "link (anyone with it can act as anyone).") from None


def cmd_phone(args: Any, console: Any) -> None:
    cfg = load_config()
    me = (cfg.get("instance_name") or "").strip()
    relay = urlparse(cfg.get("relay_url") or "")
    legacy = bool(cfg.get("relay_secret")) and not cfg.get("api_key")
    if not me or not relay.hostname:
        raise SystemExit("phone: set up Quorus first: `quorus`")
    if legacy:
        token = _participant_token(cfg, me, with_secret=getattr(args, "with_secret", False))
    else:
        from quorus_cli.agent_cmd import _bearer
        token = _bearer(cfg, cfg.get("api_key") or "", False)
    if relay.hostname not in ("127.0.0.1", "localhost", "::1"):
        # A relay your phone can already reach: just hand over the link.
        base = f"{relay.scheme}://{relay.netloc}"
        console.print(f"Open on your phone: {base}/#token=…  (link below)")
        url = f"{base}/#token={quote(token, safe='')}&name={quote(me, safe='')}"
        console.print(url, markup=False, highlight=False, soft_wrap=True)
        _print_qr(console, url)
        return
    host = lan_ip() if args.lan else tailscale_ip()
    if host is None:
        raise SystemExit(
            "phone: no Tailscale address found. Install Tailscale on this computer and your "
            "phone (https://tailscale.com) so only your own devices can reach your rooms, "
            "or run `quorus phone --lan` on a network you trust.")
    port = args.port or DEFAULT_PORT
    url = phone_url(host, port, token, me)

    async def run() -> None:
        server = await forward(host, port, relay.hostname or "127.0.0.1", relay.port or 80)
        where = "your Wi-Fi network" if args.lan else "your Tailscale network (your devices only)"
        console.print(f"[success]✓[/] Your rooms are reachable from {where} while this runs.")
        console.print("Open this on your phone. It signs you in as "
                      f"{me}, so don't share it:", markup=False)
        console.print(url, markup=False, highlight=False, soft_wrap=True)
        if not _print_qr(console, url):
            console.print("[dim]For a QR code: pip install qrcode[/]")
        if args.lan:
            console.print("[warning]![/] Anyone on this Wi-Fi with the link can read and post "
                          "as you. Prefer Tailscale on shared networks.")
        console.print("[dim]Ctrl-C stops sharing.[/]")
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[dim]stopped sharing[/]")
    except OSError as exc:
        raise SystemExit(f"phone: couldn't listen on {host}:{port} ({exc})") from None
