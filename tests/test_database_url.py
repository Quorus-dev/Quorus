"""DATABASE_URL as hosts hand it out reaches asyncpg in a form it accepts."""

from __future__ import annotations

import pytest

from quorus.storage.postgres import normalize_database_url


@pytest.mark.parametrize("raw, want", [
    # `fly postgres attach` and Heroku-style hosts set postgres:// (deployment.md).
    ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
    ("postgresql://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
    ("postgresql+psycopg2://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
    ("postgresql+asyncpg://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
    ("postgresql://u:p@h/d?sslmode=require", "postgresql+asyncpg://u:p@h/d?ssl=require"),
])
def test_normalize_database_url(raw: str, want: str) -> None:
    assert normalize_database_url(raw) == want
