"""Short-code invite endpoints.

  POST /v1/join/mint                — admin-only; creates a short code.
  GET  /v1/join/resolve/{code}      — public, rate-limited; returns payload.
  GET  /v1/join/install/{code}.sh   — public; returns a bash installer.

The mint endpoint intentionally reuses the same envelope shape the
client-side ``_encode_join_token`` produces, so the CLI's existing
``quickjoin`` pipeline can handle either path with a single decode.
"""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from quorus.auth.middleware import AuthContext, require_role, verify_auth

router = APIRouter(prefix="/v1/join", tags=["join-codes"])


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------


class MintRequest(BaseModel):
    room: str = Field(..., min_length=1, max_length=128)
    ttl_days: int = Field(default=1, ge=1, le=7)


class MintResponse(BaseModel):
    code: str
    expires_at: str
    relay_url: str
    join_url: str
    install_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request_relay_url(request: Request) -> str:
    """Reconstruct the public relay URL from the request headers.

    Prefers forwarded headers so Fly's edge proxy doesn't give us the
    internal scheme/host.
    """
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}"


def _legacy_admin_secret() -> str:
    """Return the configured RELAY_SECRET, empty string if none."""
    return os.environ.get("RELAY_SECRET", "")


def _legacy_secret_matches(token: str) -> bool:
    secret = _legacy_admin_secret()
    return bool(secret) and hmac.compare_digest(token, secret)


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------


@router.post("/mint", response_model=MintResponse)
async def mint_code(
    req: MintRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    """Create a short join code for an existing room. Admin-only."""
    require_role(auth, "admin")

    # Rate-limit to stop a leaked admin key from spamming codes.
    client_ip = request.client.host if request.client else "unknown"
    rate_svc = request.app.state.rate_limit_service
    allowed = await rate_svc.check_with_limit(
        "global", f"join-mint:{client_ip}", 20, window=60,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="rate_limited")

    tenant_id = auth.tenant_id or "_legacy"
    room_svc = request.app.state.room_service
    room_id, room_data = await room_svc.get(tenant_id, req.room)
    room_name = room_data.get("name", req.room)

    relay_url = _request_relay_url(request).rstrip("/")

    # Create a scoped invite token so joiners can call /invite/{room}/join
    # without needing admin access. The token is embedded in the short code
    # payload and lets anyone with the code join the room.
    invite_svc = request.app.state.invite_service
    invite_token = invite_svc.create_token(
        tenant_id=tenant_id,
        room_id=room_id,
        issuer=auth.sub or "admin",
        role="member",
        ttl=req.ttl_days * 86400,
    )

    # Build a payload the CLI already knows how to decode — same shape as
    # `_encode_join_token` so `quickjoin` can accept it without changes.
    payload: dict = {
        "r": relay_url,
        "n": room_name,
        "t": tenant_id,  # Tenant ID so joiners can join this tenant
        "i": invite_token,  # Scoped invite token for /invite/{room}/join
    }
    # For legacy (RELAY_SECRET) admins we ship the secret directly so the
    # recipient can auth without a key exchange; for JWT-role-admin we
    # include the tenant_id so the CLI can request to join that tenant.
    if auth.is_legacy:
        secret = _legacy_admin_secret()
        if secret:
            payload["s"] = secret

    svc = request.app.state.join_code_service
    display_code, expires_at = await svc.mint(
        tenant_id=tenant_id,
        room_id=room_id,
        room_name=room_name,
        payload=payload,
        ttl_seconds=req.ttl_days * 86400,
        created_by=auth.sub or "admin",
    )

    return MintResponse(
        code=display_code,
        expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        relay_url=relay_url,
        join_url=f"{relay_url}/v1/join/resolve/{display_code}",
        install_url=f"{relay_url}/v1/join/install/{display_code}.sh",
    )


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------


@router.get("/resolve/{code}")
async def resolve_code(
    code: str,
    request: Request,
):
    """Look up a code and return the join payload. Public, rate-limited.

    Normalizes the input — tolerant of paste artifacts (hyphens,
    quotes, whitespace, lowercase). Bad input returns 400, missing 404,
    expired 410, so the client can pick the right user-facing error.
    """
    client_ip = request.client.host if request.client else "unknown"
    rate_svc = request.app.state.rate_limit_service
    allowed = await rate_svc.check_with_limit(
        "global", f"join-resolve:{client_ip}", 100, window=60,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="rate_limited")

    svc = request.app.state.join_code_service
    from quorus.services.join_code_svc import normalize_code

    canonical = normalize_code(code)
    if canonical is None:
        raise HTTPException(status_code=400, detail="invalid code format")

    payload = await svc.resolve(canonical)
    if payload is None:
        # 404 vs 410 requires peeking behind the resolve API; the service
        # swallowed that distinction on purpose. Pick one sensible default
        # — 404, because user-facing messaging is the same either way.
        raise HTTPException(
            status_code=404, detail="code not found or expired",
        )
    return {"payload": payload, "code": canonical}


# ---------------------------------------------------------------------------
# Install script
# ---------------------------------------------------------------------------


_INSTALL_SCRIPT_TMPL = r"""#!/usr/bin/env bash
# Quorus — install + join one-liner
# Resolved from: {install_url}

set -euo pipefail

echo "→ Installing Quorus ..."

need_cmd() {{
  command -v "$1" >/dev/null 2>&1
}}

# 1. Install pipx if missing.
if ! need_cmd pipx; then
  if [[ "$(uname -s)" == "Darwin" ]] && need_cmd brew; then
    brew install pipx >/dev/null
    pipx ensurepath >/dev/null 2>&1 || true
  elif need_cmd python3; then
    python3 -m pip install --user --quiet pipx
    python3 -m pipx ensurepath >/dev/null 2>&1 || true
  else
    echo "✗ Need pipx (macOS: brew install pipx; Linux: apt install pipx)" >&2
    exit 1
  fi
  # Make pipx-installed bins visible in this shell for the rest of the script.
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Install (or upgrade) Quorus from the repo.
pipx install --force "quorus @ git+https://github.com/Quorus-dev/Quorus.git" >/dev/null

# 3. Figure out a reasonable default name — can be overridden interactively.
DEFAULT_NAME="${{USER:-user}}"
if [[ -t 0 ]]; then
  read -r -p "Your name [${{DEFAULT_NAME}}]: " NAME_INPUT || true
  NAME="${{NAME_INPUT:-$DEFAULT_NAME}}"
else
  NAME="$DEFAULT_NAME"
fi

# 4. Join the room using the short code embedded in this script.
quorus join "{code}" --name "$NAME"

# 5. Open the hub.
exec quorus
"""


@router.get("/install/{code_with_ext}", response_class=PlainTextResponse)
async def install_script(
    code_with_ext: str,
    request: Request,
):
    """Serve a bash installer tailored to a specific code.

    Same rate limit as resolve so a leaked code can't be used as a DDoS
    amplifier. The script itself is plain text with the code baked in.
    """
    client_ip = request.client.host if request.client else "unknown"
    rate_svc = request.app.state.rate_limit_service
    allowed = await rate_svc.check_with_limit(
        "global", f"join-install:{client_ip}", 100, window=60,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="rate_limited")

    # URL ends in `.sh` by convention so curl | sh feels natural; strip it.
    raw = code_with_ext
    if raw.endswith(".sh"):
        raw = raw[: -len(".sh")]

    from quorus.services.join_code_svc import normalize_code

    canonical = normalize_code(raw)
    if canonical is None:
        raise HTTPException(status_code=400, detail="invalid code format")

    svc = request.app.state.join_code_service
    payload = await svc.resolve(canonical)
    if payload is None:
        raise HTTPException(
            status_code=404, detail="code not found or expired",
        )

    relay_url = _request_relay_url(request).rstrip("/")
    # Insert the hyphen in the display code for aesthetics in the comment.
    display = (
        f"{canonical[:4]}-{canonical[4:]}"
        if len(canonical) == 8
        else canonical
    )
    script = _INSTALL_SCRIPT_TMPL.format(
        code=display,
        install_url=f"{relay_url}/v1/join/install/{display}.sh",
    )
    return PlainTextResponse(script, media_type="application/x-sh")


# ---------------------------------------------------------------------------
# Unused; shuts up lint
# ---------------------------------------------------------------------------

_ = _legacy_secret_matches
