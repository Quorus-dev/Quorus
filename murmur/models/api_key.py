"""APIKey domain model for the Murmur managed service.

No ORM dependency — pure dataclasses. Wired to asyncpg in the auth service layer.

Key format: murm_sk_<64 hex chars>  (72 chars total)
Storage:    key_prefix (first 8 hex chars) + key_hash (bcrypt(sha256(raw)))
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import bcrypt

if TYPE_CHECKING:
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# Public key prefix — registerable with GitHub Secret Scanning / GitGuardian.
_KEY_PREFIX = 'murm_sk_'
# 32 random bytes → 64 hex chars → 256 bits of entropy.
_KEY_ENTROPY_BYTES = 32
# Number of hex chars used as the lookup prefix stored in the DB.
_PREFIX_CHARS = 8
# bcrypt cost factor — chosen for ~250ms verification on a 2024 server.
_BCRYPT_ROUNDS = 12


@dataclass
class APIKey:
    """A single API key belonging to a Murmur managed-service account.

    Fields mirror the ``api_keys`` table in docs/schema.sql.
    The raw key is NEVER stored here — only ``key_hash`` and ``key_prefix``.
    """

    id: str = field(default_factory=_new_uuid)
    account_id: str = ''
    key_hash: str = ''            # bcrypt(sha256(raw_key)) — never the raw key
    key_prefix: str = ''          # first 8 hex chars of the random portion
    name: str = ''                # user-assigned label, e.g. "prod-agent"
    rate_limit_per_min: int | None = None   # None = inherit tier default
    created_at: datetime = field(default_factory=_utcnow)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    # ------------------------------------------------------------------
    # State checks
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Return True if the key has not been revoked."""
        return self.revoked_at is None

    # ------------------------------------------------------------------
    # Key generation
    # ------------------------------------------------------------------

    @classmethod
    def generate_key(
        cls,
        account_id: str,
        name: str,
        rate_limit_per_min: int | None = None,
    ) -> tuple['APIKey', str]:
        """Generate a new API key for an account.

        Returns ``(api_key_instance, raw_key)`` where:
        - ``api_key_instance`` is ready to be persisted (contains hash + prefix).
        - ``raw_key`` is the full plaintext key — return this to the user ONCE,
          then discard. It is never stored.

        Key format: murm_sk_<64 hex chars>

        Hashing: bcrypt(sha256(raw_key), rounds=12)
        SHA-256 pre-hashing is required because bcrypt silently truncates input
        at 72 bytes. Pre-hashing ensures all 256 bits of key entropy feed bcrypt.
        """
        raw_key = _KEY_PREFIX + secrets.token_hex(_KEY_ENTROPY_BYTES)
        key_prefix = raw_key[len(_KEY_PREFIX):len(_KEY_PREFIX) + _PREFIX_CHARS]

        sha_digest = hashlib.sha256(raw_key.encode()).digest()
        key_hash = bcrypt.hashpw(sha_digest, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()

        instance = cls(
            id=_new_uuid(),
            account_id=account_id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            name=name.strip(),
            rate_limit_per_min=rate_limit_per_min,
            created_at=_utcnow(),
            last_used_at=None,
            revoked_at=None,
        )
        return instance, raw_key

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    @staticmethod
    def hash_raw_key(raw_key: str) -> str:
        """Hash a raw key for storage. Use only at key creation time."""
        sha_digest = hashlib.sha256(raw_key.encode()).digest()
        return bcrypt.hashpw(sha_digest, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()

    def verify(self, raw_key: str) -> bool:
        """Return True if ``raw_key`` matches this key's stored hash.

        This is a bcrypt comparison — expect ~250ms on a 2024 server.
        Never call this in a tight loop.
        """
        sha_digest = hashlib.sha256(raw_key.encode()).digest()
        try:
            return bcrypt.checkpw(sha_digest, self.key_hash.encode())
        except Exception:
            return False

    @staticmethod
    def extract_prefix(raw_key: str) -> str:
        """Extract the 8-char lookup prefix from a raw key.

        Raises ValueError if the key does not match the murm_sk_ format.
        """
        if not raw_key.startswith(_KEY_PREFIX):
            raise ValueError(
                f'Invalid API key format — expected {_KEY_PREFIX}... prefix'
            )
        rest = raw_key[len(_KEY_PREFIX):]
        if len(rest) < _PREFIX_CHARS:
            raise ValueError('Invalid API key format — key too short')
        return rest[:_PREFIX_CHARS]

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_row(cls, row: dict) -> 'APIKey':
        """Construct an APIKey from a raw asyncpg row dict.

        Expected keys: id, account_id, key_hash, key_prefix, name,
                       rate_limit_per_min, created_at, last_used_at, revoked_at.
        """
        return cls(
            id=str(row['id']),
            account_id=str(row['account_id']),
            key_hash=row['key_hash'],
            key_prefix=row['key_prefix'],
            name=row['name'],
            rate_limit_per_min=row.get('rate_limit_per_min'),
            created_at=row['created_at'],
            last_used_at=row.get('last_used_at'),
            revoked_at=row.get('revoked_at'),
        )
