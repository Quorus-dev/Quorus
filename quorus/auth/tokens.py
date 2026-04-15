"""JWT creation/decoding and API key generation/hashing/verification."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

# ---------------------------------------------------------------------------
# Configuration (from environment)
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_TTL_SECONDS = int(os.environ.get("JWT_TTL_SECONDS", "86400"))
JWT_ISSUER = "quorus"
JWT_AUDIENCE = "quorus-relay"

_VALID_ALGORITHMS = {"HS256", "HS384", "HS512"}


def _get_jwt_secret() -> str:
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not set. Set it to a random string (min 32 bytes).")
    return JWT_SECRET


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def create_jwt(
    sub: str,
    tenant_id: str,
    tenant_slug: str,
    role: str = "user",
    participant_id: str | None = None,
    ttl: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT with standard claims (iss, aud, jti).

    Args:
        sub: Participant name (display name)
        tenant_id: Tenant UUID
        tenant_slug: Tenant slug
        role: Role (user, admin)
        participant_id: Immutable participant UUID for audit/revocation
        ttl: Token TTL in seconds (default: JWT_TTL_SECONDS)
        extra: Additional claims to include
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "tenant_id": tenant_id,
        "tenant_slug": tenant_slug,
        "role": role,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + timedelta(seconds=ttl or JWT_TTL_SECONDS),
    }
    if participant_id:
        payload["participant_id"] = participant_id
    if extra:
        payload.update(extra)
    algorithm = JWT_ALGORITHM if JWT_ALGORITHM in _VALID_ALGORITHMS else "HS256"
    return jwt.encode(payload, _get_jwt_secret(), algorithm=algorithm)


def decode_jwt(token: str) -> dict[str, Any]:
    """Decode and verify a JWT (signature, exp, iss, aud)."""
    algorithm = JWT_ALGORITHM if JWT_ALGORITHM in _VALID_ALGORITHMS else "HS256"
    return jwt.decode(
        token,
        _get_jwt_secret(),
        algorithms=[algorithm],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
    )


# ---------------------------------------------------------------------------
# API Keys — format: mct_{prefix}_{secret}
# ---------------------------------------------------------------------------

_KEY_PREFIX_LEN = 12
_KEY_SECRET_LEN = 32


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns (raw_key, prefix, key_hash) where:
    - raw_key: the full key to give to the user once (mct_{prefix}_{secret})
    - prefix: first portion for fast DB lookup
    - key_hash: bcrypt(sha256(raw_key)) for storage
    """
    prefix = secrets.token_hex(_KEY_PREFIX_LEN)[:_KEY_PREFIX_LEN]
    secret_part = secrets.token_hex(_KEY_SECRET_LEN)[:_KEY_SECRET_LEN]
    raw_key = f"mct_{prefix}_{secret_part}"
    key_hash = hash_api_key(raw_key)
    return raw_key, prefix, key_hash


def hash_api_key(raw_key: str) -> str:
    """Hash an API key for storage: bcrypt(sha256(raw_key))."""
    sha = hashlib.sha256(raw_key.encode()).digest()
    return bcrypt.hashpw(sha, bcrypt.gensalt()).decode()


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """Verify a raw API key against a stored hash."""
    sha = hashlib.sha256(raw_key.encode()).digest()
    return bcrypt.checkpw(sha, stored_hash.encode())


def extract_key_prefix(raw_key: str) -> str:
    """Extract the prefix portion from a raw API key (mct_{prefix}_{secret}).

    The prefix is everything between the first underscore and the last underscore.
    Since prefix and secret are both base64url, they may contain underscores,
    so we use the known prefix length from generation.
    """
    if not raw_key.startswith("mct_"):
        raise ValueError("Invalid API key format — expected mct_{prefix}_{secret}")
    rest = raw_key[4:]  # strip "mct_"
    # The prefix is the first _KEY_PREFIX_LEN characters
    if len(rest) < _KEY_PREFIX_LEN + 1:  # prefix + "_" + at least 1 char
        raise ValueError("Invalid API key format — expected mct_{prefix}_{secret}")
    return rest[:_KEY_PREFIX_LEN]
