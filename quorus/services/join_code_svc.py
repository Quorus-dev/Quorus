"""Short-code invite service — mint, resolve, normalize.

A short join code is 8 Crockford-alphabet characters (no 0/O/1/I/L)
rendered with a hyphen as `HX4K-M7ZP`. The hyphen is purely visual;
storage is canonical `HX4KM7ZP`. The service lives in one of two modes
depending on whether ``DATABASE_URL`` is set at boot:

- **Postgres mode** — codes persist across relay restarts, TTL
  enforced server-side. Powers the hosted Fly deployment.
- **In-memory mode** — a dict with lazy TTL eviction. Fine for dev
  and self-hosted relays that aren't wired to Postgres yet.

Both modes share the same ``JoinCodeService`` interface so routes
don't branch on storage.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

# Crockford excluding the ambiguous glyphs. 27 chars → ~38 bits for an
# 8-char code, plenty of headroom against a 100-request-per-minute-per-IP
# resolve rate limit.
_CROCKFORD_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"

# Canonical length (no hyphen) + display length (with one mid-hyphen).
_CODE_LEN = 8
_DISPLAY_HYPHEN_AT = 4


def _fmt_display(canonical: str) -> str:
    """Insert the mid-hyphen into a canonical code for human display."""
    if len(canonical) == _CODE_LEN:
        return f"{canonical[:_DISPLAY_HYPHEN_AT]}-{canonical[_DISPLAY_HYPHEN_AT:]}"
    return canonical


def normalize_code(raw: str) -> str | None:
    """Sanitize user input into a canonical code.

    Accepts the display form (``HX4K-M7ZP``), the canonical form
    (``HX4KM7ZP``), and the hostile-paste forms — uppercases, strips
    whitespace / hyphens / matched smart or straight quotes. Rejects
    anything that contains a non-Crockford character, so paste errors
    surface as "invalid code" instead of quiet 404s later.
    """
    if raw is None:
        return None
    # Strip surrounding smart+straight quotes and whitespace.
    s = raw.strip().strip("\u201c\u201d\u2018\u2019\"'")
    s = s.replace("-", "").replace(" ", "").replace("\t", "").upper()
    if len(s) != _CODE_LEN:
        return None
    if any(ch not in _CROCKFORD_ALPHABET for ch in s):
        return None
    return s


def _generate_code() -> str:
    """One random 8-char Crockford code. Collision check happens at insert."""
    return "".join(secrets.choice(_CROCKFORD_ALPHABET) for _ in range(_CODE_LEN))


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class JoinCodeService:
    """Dispatches to the Postgres or in-memory store based on DATABASE_URL.

    Route handlers hold a reference to a single instance via
    ``app.state.join_code_service`` — constructed in the relay's
    startup hook alongside the other services.
    """

    def __init__(self) -> None:
        self._use_postgres = bool(os.environ.get("DATABASE_URL"))
        self._mem: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._mem_last_gc: float = time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def mint(
        self,
        *,
        tenant_id: str,
        room_id: str | None,
        room_name: str,
        payload: dict[str, Any],
        ttl_seconds: int,
        created_by: str | None = None,
    ) -> tuple[str, datetime]:
        """Store a new code and return ``(display_code, expires_at)``."""
        ttl_seconds = max(60, min(ttl_seconds, 7 * 86400))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        # Up to 5 retries in the astronomically unlikely collision case.
        for _ in range(5):
            canonical = _generate_code()
            ok = await self._insert(
                code=canonical,
                tenant_id=tenant_id,
                room_id=room_id,
                room_name=room_name,
                payload=payload,
                expires_at=expires_at,
                created_by=created_by,
            )
            if ok:
                return _fmt_display(canonical), expires_at
        raise RuntimeError("could not mint a unique join code after 5 attempts")

    async def resolve(self, raw: str) -> dict[str, Any] | None:
        """Return the stored payload for a code, or None if missing/expired.

        The caller decides whether to return 404 (missing) vs 410
        (expired) — we collapse both to None here so storage-mode
        differences don't leak out.
        """
        canonical = normalize_code(raw)
        if canonical is None:
            return None

        if self._use_postgres:
            return await self._resolve_postgres(canonical)
        return await self._resolve_memory(canonical)

    # ------------------------------------------------------------------
    # Postgres path
    # ------------------------------------------------------------------

    async def _insert(
        self,
        *,
        code: str,
        tenant_id: str,
        room_id: str | None,
        room_name: str,
        payload: dict[str, Any],
        expires_at: datetime,
        created_by: str | None,
    ) -> bool:
        if self._use_postgres:
            return await self._insert_postgres(
                code=code,
                tenant_id=tenant_id,
                room_id=room_id,
                room_name=room_name,
                payload=payload,
                expires_at=expires_at,
                created_by=created_by,
            )
        return await self._insert_memory(
            code=code,
            tenant_id=tenant_id,
            room_id=room_id,
            room_name=room_name,
            payload=payload,
            expires_at=expires_at,
            created_by=created_by,
        )

    async def _insert_postgres(
        self,
        *,
        code: str,
        tenant_id: str,
        room_id: str | None,
        room_name: str,
        payload: dict[str, Any],
        expires_at: datetime,
        created_by: str | None,
    ) -> bool:
        from sqlalchemy.exc import IntegrityError

        from quorus.models.join_code import JoinCode
        from quorus.storage.postgres import get_db_session

        try:
            async with get_db_session() as session:
                session.add(
                    JoinCode(
                        code=code,
                        tenant_id=tenant_id,
                        room_id=room_id,
                        room_name=room_name,
                        payload=payload,
                        expires_at=expires_at,
                        created_at=datetime.now(timezone.utc),
                        created_by=created_by,
                    )
                )
        except IntegrityError:
            # PK collision — caller retries with a new generated code.
            return False
        return True

    async def _resolve_postgres(self, code: str) -> dict[str, Any] | None:
        from sqlalchemy import select

        from quorus.models.join_code import JoinCode
        from quorus.storage.postgres import get_db_session

        async with get_db_session() as session:
            row = await session.execute(
                select(JoinCode).where(JoinCode.code == code)
            )
            entry = row.scalar_one_or_none()
        if entry is None:
            return None
        if entry.expires_at < datetime.now(timezone.utc):
            return None
        return dict(entry.payload)

    # ------------------------------------------------------------------
    # In-memory path
    # ------------------------------------------------------------------

    async def _insert_memory(
        self,
        *,
        code: str,
        tenant_id: str,
        room_id: str | None,
        room_name: str,
        payload: dict[str, Any],
        expires_at: datetime,
        created_by: str | None,
    ) -> bool:
        async with self._lock:
            self._maybe_gc_locked()
            if code in self._mem:
                return False
            self._mem[code] = {
                "tenant_id": tenant_id,
                "room_id": room_id,
                "room_name": room_name,
                "payload": payload,
                "expires_at": expires_at,
                "created_at": datetime.now(timezone.utc),
                "created_by": created_by,
            }
            return True

    async def _resolve_memory(self, code: str) -> dict[str, Any] | None:
        async with self._lock:
            self._maybe_gc_locked()
            entry = self._mem.get(code)
            if entry is None:
                return None
            if entry["expires_at"] < datetime.now(timezone.utc):
                self._mem.pop(code, None)
                return None
            return dict(entry["payload"])

    def _maybe_gc_locked(self) -> None:
        """Evict expired entries — only run every ~60s to keep resolve cheap."""
        now = time.monotonic()
        if now - self._mem_last_gc < 60:
            return
        self._mem_last_gc = now
        cutoff = datetime.now(timezone.utc)
        expired = [k for k, v in self._mem.items() if v["expires_at"] < cutoff]
        for k in expired:
            self._mem.pop(k, None)
