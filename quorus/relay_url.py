"""Resolve the public relay URL for a request, safely.

``X-Forwarded-Host`` / ``X-Forwarded-Proto`` are attacker-controlled unless
every hop is trusted. Any handler that bakes the relay URL into something a
client will execute or copy-paste (the join installer, the signup
``setup_command``) must go through :func:`public_relay_url` so a poisoned
header cannot inject shell/URL metacharacters.
"""

from __future__ import annotations

import os
import re

from fastapi import HTTPException, Request

# Host charset we accept: letters, digits, dots, hyphens and an optional
# ``:port``. Deliberately strict — quotes, ``$()``, semicolons, spaces and
# slashes are all rejected.
_ALLOWED_RELAY_SCHEMES = ("http", "https")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:\d+)?$")


def validate_relay_url(value: str) -> str:
    """Return ``value`` if it is a bare ``http(s)://host[:port]`` URL.

    Raises HTTP 400 otherwise. This is the single choke point that keeps a
    client-controlled forwarded header out of anything that gets executed.
    """
    scheme, sep, host = value.partition("://")
    scheme = scheme.lower()
    if (
        not sep
        or scheme not in _ALLOWED_RELAY_SCHEMES
        or not _HOST_RE.match(host)
    ):
        raise HTTPException(status_code=400, detail="invalid relay host")
    return f"{scheme}://{host}"


def public_relay_url(request: Request) -> str:
    """Reconstruct the public relay URL from the request, safely.

    Prefers the operator-configured ``QUORUS_PUBLIC_RELAY`` when set; otherwise
    rebuilds the URL from the forwarded headers and only accepts a strict
    ``http(s)://host[:port]`` shape, returning 400 on anything else.
    """
    configured = (os.environ.get("QUORUS_PUBLIC_RELAY") or "").strip().rstrip("/")
    if configured:
        return validate_relay_url(configured)

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).strip()
    # Proxies append, so the left-most entry is the client-facing host.
    host = (
        request.headers.get("x-forwarded-host", request.url.netloc)
        .split(",")[0]
        .strip()
    )
    return validate_relay_url(f"{scheme}://{host}")
