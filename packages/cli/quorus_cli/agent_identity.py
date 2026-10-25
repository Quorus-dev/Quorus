"""An agent's own participant key, claimed before its daemon starts, and the
identity a reviewer pin binds to (docs/plans/gate-by-default.md, Codex plan GO
eb25acef).

On the owner's own relay the daemon already signs in as itself: in legacy-bearer
mode it claims ``/v1/auth/claim-name`` with the relay secret and saves the key in
``<config>/runtime/agents/<name>`` (``scripts/reflexd.py``, ``_participant_jwt``),
so its posts are stamped ``key``. Claiming that same key here, into that same
place, just does it before the daemon starts, which is what lets the reviewer be
pinned before any agent can publish. The daemon then finds the key and reuses it.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from quorus import bundle_client
from quorus_cli import relay_http as _relay_http


class IdentityError(RuntimeError):
    """The agent's own key couldn't be claimed or read: no pin, no gate."""


def key_dir(name: str, state_dir: Path | None = None) -> Path:
    """Where *name*'s daemon loads its participant key: its config dir's
    ``runtime/agents/<name>`` (the daemon's ``key_dir``)."""
    from quorus.runtime import bindings

    base = state_dir if state_dir is not None else bindings.config_dir()
    return base / "runtime" / "agents" / name


def ensure_key(relay_url: str, secret: str, name: str,
               state_dir: Path | None = None) -> str:
    """*name*'s saved key, or one claimed now with the relay *secret* and saved
    where its daemon looks. Never follows a redirect with a credential."""
    where = key_dir(name, state_dir)
    key = bundle_client.load_participant_key(relay_url, name, where)
    if key:
        return key
    try:
        resp = _relay_http.post(f"{relay_url.rstrip('/')}/v1/auth/claim-name",
                                json={"name": name},
                                headers={"Authorization": f"Bearer {secret}"},
                                timeout=10, follow_redirects=False)
    except Exception as exc:  # network, TLS, a refused connection
        raise IdentityError(f"couldn't reach the relay to claim @{name} ({exc})") from exc
    if resp.status_code == 409:
        raise IdentityError(f"@{name} is already claimed on this relay and its key isn't "
                            "saved here")
    if resp.status_code != 200:
        raise IdentityError(f"the relay wouldn't claim @{name} (HTTP {resp.status_code})")
    try:
        key = str(resp.json()["participant_key"])
    except (ValueError, KeyError, TypeError) as exc:
        raise IdentityError(f"the relay's answer for @{name} had no key") from exc
    bundle_client.save_participant_key(relay_url, name, key, where)
    return key


def identity(relay_url: str, key: str) -> dict[str, str]:
    """``{"tenant", "claim_id"}`` from the token *key* signs in as: the
    ``pk_claim`` the relay stamps on the agent's messages as ``sender_claim_id``.
    Read from our own token; the relay verifies it, we only decode it."""
    try:
        resp = _relay_http.post(f"{relay_url.rstrip('/')}/v1/auth/token",
                                json={"api_key": key}, timeout=10, follow_redirects=False)
    except Exception as exc:
        raise IdentityError(f"couldn't sign the agent in ({exc})") from exc
    if resp.status_code != 200:
        raise IdentityError(f"the relay wouldn't sign the agent in (HTTP {resp.status_code})")
    try:
        body = resp.json()["token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise IdentityError("the agent's token couldn't be read") from exc
    claim, tenant = claims.get("pk_claim"), claims.get("tenant_id")
    if not claim:
        raise IdentityError("the agent's token names no key claim")
    return {"tenant": str(tenant or ""), "claim_id": str(claim)}
