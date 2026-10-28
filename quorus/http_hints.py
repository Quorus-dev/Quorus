"""Say why the relay refused, not just its status code (U1, 0.5.1-0.5.2).

The CLI printed "Could not create room · server returned HTTP 422", "hint:
HTTP 413" and "hint: HTTP 429": the user had to guess the name rule, the size
limit and when to retry (overseer's agent sweep of quorus-ai 0.5.0). The MCP
tools did the same for agents ("Relay returned 429: {...}"), so both the CLI
and the MCP server use this one wording.
"""

from __future__ import annotations

from typing import Any

import httpx

_MAX_DETAIL = 200


def _detail(resp: Any) -> str:
    """The relay's own reason: FastAPI's ``detail`` string, or the messages
    of a validation error (``[{"msg": ...}]``). Empty when there is none."""
    try:
        body = resp.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):
        msgs = [str(d.get("msg", "")) for d in detail if isinstance(d, dict)]
        detail = "; ".join(m.removeprefix("Value error, ") for m in msgs if m)
    return str(detail or "").strip()[:_MAX_DETAIL]


def hint(resp: Any) -> str:
    """One line saying why *resp* failed and what to do about it."""
    code = resp.status_code
    detail = _detail(resp)
    if code == 429:
        wait = (resp.headers.get("retry-after") or "").strip()
        if wait.isdigit():
            return f"the relay is rate-limiting you: try again in {wait}s"
        return f"the relay is rate-limiting you: {detail or 'try again in a minute'}"
    if code == 413:
        return detail or ("too large for this relay (MAX_MESSAGE_SIZE, 50 KB by default)")
    if code in (400, 422) and detail:
        return detail
    return f"HTTP {code}: {detail}" if detail else f"HTTP {code}"


# A relay without a database (shared-secret, file mode) has no API-key
# accounts, so no key can ever sign in there. It says so with this header on
# its 401, and clients tell "key auth is impossible here" from "bad key".
AUTH_MODE_HEADER = "X-Quorus-Auth-Mode"
SECRET_AUTH_ONLY = "secret"
_NO_ACCOUNTS_PHRASE = "no API-key accounts"  # 0.7.10-0.7.12 relays: detail only


def key_auth_unavailable(resp: Any) -> bool:
    """True when *resp* is the relay saying it has no API-key accounts at all
    (not that this particular key is wrong, revoked, or unknown)."""
    if resp.status_code != 401:
        return False
    if (resp.headers.get(AUTH_MODE_HEADER) or "").strip().lower() == SECRET_AUTH_ONLY:
        return True
    return _NO_ACCOUNTS_PHRASE in _detail(resp)


def rejected_key_message(resp: Any, *, relay_url: str, key_from: str) -> str:
    """Why the relay refused an API key and what to change. ``key_from`` names
    where the key came from (an env var name, or "" for the saved config).
    Never includes the key."""
    if key_from:
        where = f"the API key in your {key_from} environment variable"
        fix = (f"unset {key_from} (or set {key_from}=\"\") in the shell that starts "
               "this program, then restart it")
    else:
        where = "the api_key saved in your Quorus config"
        fix = "re-run `quorus init` or `quorus join` for this relay"
    if key_auth_unavailable(resp):
        return (f"the relay at {relay_url} runs on its relay secret and has no API-key "
                f"accounts, so it refused {where}. To use the relay secret instead, {fix}.")
    if resp.status_code >= 500 and key_from:
        return (f"the relay at {relay_url} failed to sign in {where} ({hint(resp)}). If "
                f"this relay runs on a relay secret (no database), {fix}.")
    return f"the relay at {relay_url} refused {where} ({hint(resp)}). To fix it, {fix}."


class ApiKeyRejected(httpx.HTTPStatusError):
    """The relay refused an API key at /v1/auth/token. The message (from
    :func:`rejected_key_message`) says where the key came from and what to
    change; it never holds the key."""
