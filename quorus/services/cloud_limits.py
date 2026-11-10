"""Caps for a relay that anyone on the internet can sign up to (Quorus Cloud).

A self-hosted relay serves people its owner chose, so every cap here is off
unless its env var is set. On a public relay, the per-minute signup and
room-creation limits still allow thousands a day from one address. These bound
what a stranger, or a script, can make the operator pay to store:

- ``SIGNUPS_PER_IP_PER_DAY``: new workspaces one address may create a day.
- ``MAX_TENANTS``: workspaces the relay holds in total. Past it, signup says the
  relay is full instead of growing the bill.
- ``MAX_ROOMS_PER_TENANT``: rooms one workspace may hold.

Joining an existing workspace with an invite never counts against the first two:
the inviter already paid for that workspace.
"""

from __future__ import annotations

import os

from fastapi import HTTPException

DAY_S = 86400


def env_cap(name: str) -> int:
    """The cap in *name*, or 0 (off) when unset or not a positive integer."""
    try:
        return max(0, int(os.environ.get(name, "0") or "0"))
    except ValueError:
        return 0


async def check_new_workspace(rate_limit_svc, client_ip: str, tenant_count) -> None:
    """Refuse a new workspace past the daily per-address or total cap.

    *tenant_count* is an async callable, so the count query only runs when
    ``MAX_TENANTS`` is set."""
    per_day = env_cap("SIGNUPS_PER_IP_PER_DAY")
    if per_day and not await rate_limit_svc.check_with_limit(
            "global", f"signup-day:{client_ip}", per_day, window=DAY_S):
        raise HTTPException(
            status_code=429,
            detail=f"This address made {per_day} workspaces today; try again tomorrow.",
            headers={"Retry-After": str(DAY_S)})
    total = env_cap("MAX_TENANTS")
    if total and await tenant_count() >= total:
        raise HTTPException(
            status_code=503,
            detail="This relay is full and isn't taking new workspaces. Run your own "
                   "with `quorus relay`, or ask a teammate for an invite.")


async def check_room_quota(room_svc, tenant_id: str) -> None:
    """Refuse a room past ``MAX_ROOMS_PER_TENANT``."""
    cap = env_cap("MAX_ROOMS_PER_TENANT")
    if cap and len(await room_svc.list_all(tenant_id)) >= cap:
        raise HTTPException(
            status_code=403,
            detail=f"This workspace has {cap} rooms, the most this relay allows. "
                   "Destroy one you're done with: quorus destroy <room>.")
