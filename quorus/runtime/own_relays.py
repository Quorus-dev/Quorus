"""Relays this machine's owner runs, recorded where this machine starts one.

The record says "this relay is mine": on it the owner's own name is trusted
without a signature (owner_sig), and an owner who signs in with a participant
key isn't shown a teammate's relay (agent_cmd). Split out of owner_sig so the
record needs nothing from the signing code.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit


def normalize_relay(url: str) -> str:
    """One string per relay, on ``config.same_relay``'s rules: scheme, host, port
    (the default one written out) and base path all count, so ``/alice`` and
    ``/mallory`` on one host are different relays (codex, review of 86972324). An
    IPv6 host keeps its brackets, so ``[::1]:8080`` can't read as ``[::1:8080]``.
    localhost and 127.0.0.1 stay different: fail closed rather than guess.
    Anything malformed, or carrying credentials, a query or a fragment, is ""."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme.lower())
    except ValueError:  # a malformed port or IPv6 literal
        return ""
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if (scheme not in ("http", "https") or not host or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment):
        return ""
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}{parts.path.rstrip('/')}"


def own_relays_path() -> Path:
    return Path.home() / ".quorus" / "own_relays.json"


def own_relays(path: Path) -> set[str]:
    """The relays where the owner's name alone is trusted, as recorded on this
    machine. Missing or unreadable: none, so every relay needs a signature."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {u for u in (normalize_relay(str(x)) for x in data) if u}


def add_own_relay(path: Path, url: str) -> None:
    """Record *url* as a relay this machine runs. Called only where this machine
    starts its own relay, never on joining someone else's (Codex)."""
    from quorus.private_files import write_private_json

    norm = normalize_relay(url)
    if not norm:
        raise ValueError(f"not a relay URL: {url!r}")
    write_private_json(path, sorted(own_relays(path) | {norm}))


def owned_alias(url: str, path: Path | None = None) -> str:
    """*url*, or the exact loopback address this machine's relay owns on the same
    port, when *url* says `localhost` but the relay holds only 127.0.0.1 or ::1.

    `localhost` is trusted only when the relay holds both loopbacks (relay.
    own_relay_urls): it may resolve to either, and the other could belong to
    another process. Pointing at the address the relay did bind keeps that
    boundary and still lets the owner use their own relay (Codex on 21d422ff,
    0.7.1 stranger path). Anything else comes back unchanged."""
    owned = own_relays(path or own_relays_path())
    norm = normalize_relay(url)
    if not norm or norm in owned:
        return url
    parts = urlsplit(url.strip())
    if (parts.hostname or "").lower() != "localhost":
        return url
    for host in ("127.0.0.1", "[::1]"):
        alias = norm.replace("://localhost:", f"://{host}:", 1)
        if alias in owned:
            return alias
    return url
