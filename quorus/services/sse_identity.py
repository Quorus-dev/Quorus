"""Bind SSE capabilities and live deliveries to participant-key generations.

The opaque one-use token remains stored in the configured token backend. Its
backend recipient includes the claim generation, so changing the public wrapper
cannot grant a different generation. Old unbound tokens cannot open a claimed
name's stream. The shared relay secret keeps its explicit operator authority.
"""
from __future__ import annotations

import asyncio
import re

from fastapi import Request

PREFIX = "qst1."
_CLAIM = re.compile(r"[0-9a-f]{32}")


def recipient_key(recipient: str, claim: str | None) -> str:
    return f"{recipient}:claim:{claim}" if claim else recipient


def wrap(token: str, claim: str | None) -> str:
    return f"{PREFIX}{claim}.{token}" if claim else token


def unwrap(token: str) -> tuple[str | None, str]:
    if not token.startswith(PREFIX):
        return None, token
    parts = token[len(PREFIX):].split(".", 1)
    if len(parts) != 2 or not _CLAIM.fullmatch(parts[0]) or not parts[1]:
        return None, ""
    return parts[0], parts[1]


async def current_claim(request: Request, recipient: str) -> str | None:
    store = getattr(request.app.state, "participant_keys", None)
    if store is None:
        return None
    info = await asyncio.to_thread(store.info, recipient)
    return info["claim_id"] if info else None


async def current(request: Request, tenant: str, recipient: str, claim: str | None) -> bool:
    if tenant != "_legacy":
        return claim is None  # participant keys belong only to the legacy tenant
    store = getattr(request.app.state, "participant_keys", None)
    if claim is not None:
        return bool(store) and await asyncio.to_thread(store.is_current, recipient, claim)
    # Pre-upgrade and unclaimed-name tokens never inherit a subsequently claimed name.
    return await current_claim(request, recipient) is None


async def visible_message(request: Request, tenant: str, recipient: str, message: dict) -> bool:
    """A queued room event is not permission to read after a kick/revoke."""
    room = message.get("room")
    if not room:
        return True  # direct messages are scoped by recipient identity
    try:
        _, data = await request.app.state.room_service.get(tenant, room)
    except Exception:
        return False
    members = data.get("members") or {}
    if recipient not in members:
        return False
    store = getattr(request.app.state, "participant_keys", None)
    if store is not None:
        from quorus.auth.member_agents import owner_of

        owner = await asyncio.to_thread(owner_of, store, recipient)
        if owner is not None and owner not in members:
            return False
    return True
