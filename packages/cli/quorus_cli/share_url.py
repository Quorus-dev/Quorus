"""Validate the address printed for teammates, without changing the mint relay."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                   r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?\Z")
_INVALID = ("--relay-url must be an http(s) URL with only a host and optional port "
            "(no path, query, fragment, or credentials)")


def is_loopback_url(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Also recognize IPv4 shorthand/integer forms without a DNS lookup.
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        mapped = getattr(address, "ipv4_mapped", None)
        return (address.is_loopback or address.is_unspecified
                or bool(mapped and (mapped.is_loopback or mapped.is_unspecified)))
    except (ValueError, OSError):
        return False


def public_relay_url(value: str) -> str:
    # People paste origins with one trailing slash; accept that, nothing longer.
    if isinstance(value, str) and value.endswith("/") and not value.endswith("//"):
        value = value[:-1]
    if not isinstance(value, str) or any(c.isspace() for c in value) or any(
            c in value for c in ("?", "#", "\\", "%")):
        raise ValueError(_INVALID)
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port  # invalid/out-of-range ports raise ValueError
        if (url.scheme not in ("http", "https") or not host or url.path
                or url.username is not None or url.password is not None
                or url.netloc.endswith(":") or port == 0):
            raise ValueError(_INVALID)
        if ":" in host:
            ipaddress.IPv6Address(host)
        elif len(host) > 253 or not _HOST.fullmatch(host):
            raise ValueError(_INVALID)
    except ValueError:
        raise ValueError(_INVALID) from None
    if is_loopback_url(value):
        raise ValueError("--relay-url must be reachable from another machine, not loopback "
                         "or an unspecified bind address")
    return value
