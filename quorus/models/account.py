"""Account domain model for the Quorus managed service.

No ORM dependency — pure dataclasses. Wired to asyncpg in the auth service layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class AccountTier(str, Enum):
    """Pricing tier for a managed-service account.

    Values match the Postgres account_tier enum defined in docs/schema.sql.
    Inherits str so that tier values serialize to JSON strings without extra
    configuration in FastAPI response models.
    """

    FREE = 'free'
    PRO = 'pro'
    ENTERPRISE = 'enterprise'

    @property
    def rate_limit_per_min(self) -> int | None:
        """Default request rate limit for this tier (None = unlimited)."""
        _limits: dict[AccountTier, int | None] = {
            AccountTier.FREE: 60,
            AccountTier.PRO: 600,
            AccountTier.ENTERPRISE: None,
        }
        return _limits[self]

    @property
    def max_keys(self) -> int | None:
        """Maximum number of active API keys allowed (None = unlimited)."""
        _limits: dict[AccountTier, int | None] = {
            AccountTier.FREE: 3,
            AccountTier.PRO: 20,
            AccountTier.ENTERPRISE: None,
        }
        return _limits[self]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class Account:
    """A Quorus managed-service account.

    Immutable after creation: ``id`` is a UUID assigned at signup and never
    changes. ``email`` and ``tier`` are mutable via dedicated service methods
    (not free-form PATCH).

    Fields mirror the ``accounts`` table in docs/schema.sql.
    """

    id: str = field(default_factory=_new_uuid)
    email: str = ''
    tier: AccountTier = AccountTier.FREE
    created_at: datetime = field(default_factory=_utcnow)
    deleted_at: datetime | None = None

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Return True if the account has not been soft-deleted."""
        return self.deleted_at is None

    @property
    def rate_limit_per_min(self) -> int | None:
        """Effective rate limit for this account based on its tier."""
        return self.tier.rate_limit_per_min

    @property
    def max_keys(self) -> int | None:
        """Maximum number of active API keys allowed for this account."""
        return self.tier.max_keys

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, email: str, tier: AccountTier = AccountTier.FREE) -> 'Account':
        """Create a new account with a fresh UUID and current timestamp."""
        return cls(
            id=_new_uuid(),
            email=email.strip().lower(),
            tier=tier,
            created_at=_utcnow(),
            deleted_at=None,
        )

    @classmethod
    def from_row(cls, row: dict) -> 'Account':
        """Construct an Account from a raw asyncpg row dict.

        Expected keys: id, email, tier, created_at, deleted_at.
        """
        return cls(
            id=str(row['id']),
            email=row['email'],
            tier=AccountTier(row['tier']),
            created_at=row['created_at'],
            deleted_at=row.get('deleted_at'),
        )
