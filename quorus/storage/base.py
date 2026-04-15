"""Shared SQLAlchemy declarative ``Base`` for all ORM models.

Lives here (rather than in any one models module) so that admin, audit, outbox
and future model packages can all attach to the same metadata without creating
import cycles between them.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in Quorus."""

    pass
