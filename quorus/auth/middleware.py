"""Auth middleware — JWT-first verification with legacy RELAY_SECRET fallback."""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select

from quorus.auth.tokens import decode_jwt

logger = logging.getLogger("quorus.auth")

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
    participant_id: str | None = None  # immutable participant ID
    tenant_id: str | None = None
    tenant_slug: str | None = None
    role: str = "user"
    is_legacy: bool = False
    claims: dict = field(default_factory=dict)


async def refresh_revocation_cache(session) -> None:
    """Reload revoked API key prefixes from the database."""
    global _revoked_prefixes, _revoked_refreshed_at
    from quorus.admin.models import ApiKey

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

        # Check revocation cache if Postgres is available
        key_prefix = claims.get("key_prefix")
        if key_prefix and DATABASE_URL:
            if _needs_revocation_refresh():
                try:
                    from quorus.storage.postgres import get_db_session

                    async with get_db_session() as session:
                        await refresh_revocation_cache(session)
                except Exception:
                    # DB unavailable — fail-closed for admin routes,
                    # allow stale cache for regular users
                    if claims.get("role") == "admin" and _revoked_refreshed_at is None:
                        raise HTTPException(
                            status_code=503,
                            detail="Cannot verify key revocation status — try again later",
                        )
                    logger.warning("Revocation cache refresh failed, using stale cache")
            if key_prefix in _revoked_prefixes:
                raise HTTPException(
                    status_code=401,
                    detail="API key has been revoked",
                )

        return AuthContext(
            sub=claims.get("sub"),
            participant_id=claims.get("participant_id"),
            tenant_id=claims.get("tenant_id"),
            tenant_slug=claims.get("tenant_slug"),
            role=claims.get("role", "user"),
            is_legacy=False,
            claims=claims,
        )
    except HTTPException:
        raise  # Re-raise revocation 401
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


def require_participant_id(auth: AuthContext, expected_id: str | None = None) -> str:
    """Enforce that the authenticated user has a valid participant_id.

    Legacy (RELAY_SECRET) users skip this check — they have admin-level access.
    Returns the participant_id if valid.

    Use this for audit-sensitive operations where immutable identity is required.
    """
    if auth.is_legacy:
        # Legacy auth doesn't have participant_id — this is acceptable for admin ops
        # but caller should handle None case if needed for audit
        raise HTTPException(
            status_code=403,
            detail="Operation requires account-based identity (not legacy auth)",
        )
    if auth.participant_id is None:
        raise HTTPException(
            status_code=403,
            detail="Token missing participant_id claim — reauthenticate with new API key",
        )
    if expected_id and auth.participant_id != expected_id:
        raise HTTPException(
            status_code=403,
            detail="Identity mismatch: token participant_id != expected",
        )
    return auth.participant_id
