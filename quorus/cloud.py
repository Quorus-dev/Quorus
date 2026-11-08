"""Quorus Cloud: the hosted relay people on different computers share rooms on.

A laptop relay listens on 127.0.0.1, so a friend on another computer can't
reach it. Quorus Cloud is an account-mode relay (Postgres, one workspace per
signup, its own key per person and per agent). Anyone can make a room there and
invite people from anywhere. Their agents still run on their own machines: the
relay carries room text, and code moves only when someone shares a branch.

``QUORUS_PUBLIC_RELAY`` points everything here at another hosted relay, and
``off`` turns the fallback off.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass

import httpx

DEFAULT_PUBLIC_RELAY = "https://cloud.quorus.dev"
_OFF = ("", "0", "off", "none", "false")


def public_relay() -> str:
    """The hosted relay's origin, or "" when ``QUORUS_PUBLIC_RELAY`` turns it off."""
    raw = os.environ.get("QUORUS_PUBLIC_RELAY")
    if raw is None:
        return DEFAULT_PUBLIC_RELAY
    return "" if raw.strip().lower() in _OFF else raw.strip().rstrip("/")


def code_relays(explicit: str | None, configured: str | None) -> list[str]:
    """Where to look up a short invite code, in order.

    An explicit ``--relay`` is the only place. Otherwise try this machine's own
    relay first, since a teammate on the same relay is the common case, then
    Quorus Cloud, where codes from people on other computers live. A code exists
    on just one relay, so at most one of these answers."""
    if explicit:
        return [explicit.rstrip("/")]
    out: list[str] = []
    for url in ((configured or "").rstrip("/"), public_relay()):
        if url and url not in out:
            out.append(url)
    return out


def workspace_for(name: str) -> str:
    """A workspace slug for *name* that won't collide with someone else's
    (signup's rule: 2-64 lowercase letters, digits, '-' or '_')."""
    base = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:40] or "ws"
    return f"{base}-{secrets.token_hex(3)}"


@dataclass(frozen=True)
class Account:
    relay_url: str
    name: str
    workspace: str
    api_key: str


class SignupError(Exception):
    """Signup failed; the message is meant for the person running it."""


def signup(relay_url: str, name: str, *, attempts: int = 3) -> Account:
    """Create a workspace for *name* on *relay_url* and return its key.

    ``X-Quorus-Setup-Local`` asks the relay to return the key in the body, once.
    A taken workspace name is retried with a fresh suffix."""
    relay_url = relay_url.rstrip("/")
    for _ in range(attempts):
        workspace = workspace_for(name)
        try:
            resp = httpx.post(f"{relay_url}/v1/auth/signup",
                              json={"name": name, "workspace": workspace},
                              headers={"X-Quorus-Setup-Local": "1"},
                              timeout=20, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise SignupError(f"can't reach {relay_url} ({type(exc).__name__})") from None
        if resp.status_code == 409:
            continue
        if resp.status_code != 200:
            raise SignupError(_detail(resp))
        key = (resp.json() or {}).get("api_key") or ""
        if not key:
            raise SignupError("the relay created the account but returned no key")
        return Account(relay_url, name, workspace, key)
    raise SignupError("couldn't find a free workspace name; try again")


def join_workspace(relay_url: str, name: str, tenant_id: str, invite_token: str) -> str:
    """Spend an account-mode invite (a code's ``t`` and ``i``): join the
    inviter's workspace as *name* and return this person's own key."""
    try:
        resp = httpx.post(f"{relay_url.rstrip('/')}/v1/auth/signup",
                          json={"name": name, "workspace": workspace_for(name),
                                "join_tenant_id": tenant_id, "invite_token": invite_token},
                          headers={"X-Quorus-Setup-Local": "1"},
                          timeout=20, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise SignupError(f"can't reach {relay_url} ({type(exc).__name__})") from None
    if resp.status_code == 409:
        raise SignupError(f"@{name} is taken in that workspace; pick another name")
    if resp.status_code != 200:
        raise SignupError(_detail(resp))
    key = (resp.json() or {}).get("api_key") or ""
    if not key:
        raise SignupError("the relay accepted the invite but returned no key")
    return key


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code == 400 and "no accounts" in str(body.get("detail", "")):
        return "that relay doesn't take signups (it runs on a shared secret)"
    if resp.status_code == 422:
        return "names are 1-64 letters, digits, '-' or '_'"
    text = body.get("detail") or body.get("error") or resp.text[:200]
    return f"signup failed (HTTP {resp.status_code}): {text}"
