"""JWT-based room invite service.

Replaces the legacy HMAC invite tokens with proper JWT claims:
type, tenant_id, room_id, issuer, role, nonce, exp.
"""

from __future__ import annotations

import time
import uuid

import jwt
from fastapi import HTTPException


class InviteService:
    """Create and verify JWT invite tokens for room joins."""

    _ALGORITHM = "HS256"

    def __init__(
        self,
        secret: str,
        default_ttl: int = 86400,
    ) -> None:
        if not secret:
            raise ValueError("Invite secret must not be empty")
        self._secret = secret
        self._default_ttl = default_ttl

    def create_token(
        self,
        tenant_id: str,
        room_id: str,
        issuer: str,
        role: str = "member",
        ttl: int | None = None,
    ) -> str:
        """Return a signed JWT invite token."""
        now = int(time.time())
        ttl = ttl if ttl is not None else self._default_ttl
        payload = {
            "type": "room_invite",
            "tenant_id": tenant_id,
            "room_id": room_id,
            "iss": issuer,
            "role": role,
            "nonce": uuid.uuid4().hex,
            "iat": now,
            "exp": now + ttl,
        }
        return jwt.encode(payload, self._secret, algorithm=self._ALGORITHM)

    def verify_token(self, token: str) -> dict:
        """Decode and validate an invite token.

        Returns the decoded claims dict on success.
        Raises ``HTTPException(403)`` on any failure.
        """
        try:
            claims = jwt.decode(
                token, self._secret, algorithms=[self._ALGORITHM]
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=403, detail="Invite token has expired"
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=403, detail="Invalid invite token"
            )
        if claims.get("type") != "room_invite":
            raise HTTPException(
                status_code=403, detail="Invalid invite token type"
            )
        return claims
