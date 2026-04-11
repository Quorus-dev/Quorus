"""Auth middleware — JWT-first verification with legacy RELAY_SECRET fallback."""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select

from murmur.auth.tokens import decode_jwt

logger = logging.getLogger("murmur.auth")

RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Default ALLOW_LEGACY_AUTH to false when DATABASE_URL is set (production),
# true otherwise (dev/test).
_legacy_default = "false" if DATABASE_URL else "true"
ALLOW_LEGACY_AUTH = os.environ.get("ALLOW_LEGACY_AUTH", _legacy_default).lower() in (
    "1",
    "true",
    "yes",
)

# Cache of revoked key prefixes — refreshed periodically
_revoked_prefixes: set[str] = set()
_revoked_refreshed_at: datetime | None = None
_REVOCATION_CACHE_TTL = 300  # 5 minutes


@dataclass
class AuthContext:
    """Verified identity from the request."""

    sub: str | None = None  # participant name (None for legacy/admin)
    tenant_id: str | None = None
    tenant_slug: str | None = None
    role: str = "user"
    is_legacy: bool = False
    claims: dict = field(default_factory=dict)


async def refresh_revocation_cache(session) -> None:
    """Reload revoked API key prefixes from the database."""
    global _revoked_prefixes, _revoked_refreshed_at
    from murmur.admin.models import ApiKey

    result = await session.execute(
        select(ApiKey.key_prefix).where(ApiKey.revoked_at.isnot(None))
    )
    _revoked_prefixes = {row[0] for row in result.fetchall()}
    _revoked_refreshed_at = datetime.now(timezone.utc)
    logger.debug("Refreshed revocation cache: %d revoked keys", len(_revoked_prefixes))


def _needs_revocation_refresh() -> bool:
    if _revoked_refreshed_at is None:
        return True
    age = (datetime.now(timezone.utc) - _revoked_refreshed_at).total_seconds()
    return age > _REVOCATION_CACHE_TTL


async def verify_auth(request: Request) -> AuthContext:
    """FastAPI dependency — verify JWT or legacy Bearer token.

    Returns an AuthContext with the verified identity.
    Raises HTTPException(401) on failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]  # strip "Bearer "

    # Try JWT first
    try:
        claims = decode_jwt(token)
        return AuthContext(
            sub=claims.get("sub"),
            tenant_id=claims.get("tenant_id"),
            tenant_slug=claims.get("tenant_slug"),
            role=claims.get("role", "user"),
            is_legacy=False,
            claims=claims,
        )
    except Exception:
        pass  # Not a valid JWT — try legacy fallback

    # Legacy fallback: RELAY_SECRET as Bearer token
    if RELAY_SECRET and hmac.compare_digest(token, RELAY_SECRET):
        if DATABASE_URL and not ALLOW_LEGACY_AUTH:
            raise HTTPException(
                status_code=401,
                detail="Legacy auth disabled in production mode. Use API key + JWT.",
            )
        return AuthContext(
            sub=None,
            tenant_id=None,
            tenant_slug=None,
            role="admin",
            is_legacy=True,
        )

    logger.warning(
        "Auth failure from %s",
        request.client.host if request.client else "unknown",
    )
    raise HTTPException(status_code=401, detail="Invalid or missing auth token")


def require_identity(auth: AuthContext, expected_name: str | None = None) -> None:
    """Enforce that the authenticated user matches the expected identity.

    Legacy (RELAY_SECRET) users skip this check — they have admin-level access.
    """
    if auth.is_legacy:
        return  # Legacy auth skips identity enforcement
    if auth.sub is None:
        raise HTTPException(status_code=403, detail="Token missing subject claim")
    if expected_name and auth.sub != expected_name:
        raise HTTPException(
            status_code=403,
            detail=f"Identity mismatch: token subject '{auth.sub}' != '{expected_name}'",
        )


def require_role(auth: AuthContext, *roles: str) -> None:
    """Require the authenticated user has one of the given roles."""
    if auth.is_legacy:
        return  # Legacy auth is treated as admin
    if auth.role not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"Requires role {' or '.join(roles)}, got '{auth.role}'",
        )
