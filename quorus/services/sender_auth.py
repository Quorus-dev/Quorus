"""How a room message's sender proved who it is, stamped on the stored message.

Built only from the request's verified AuthContext, never from the request
body. A shared-secret caller names itself (``from_name`` is whatever it sent),
so ``"secret"`` proves nothing about who spoke. A participant key's JWT carries
the claim generation, so a name that was released and claimed again by someone
else gets a different ``sender_claim_id``. A hosted login carries the immutable
participant id. Loop B trusts an owner verdict only when the stamp matches the
owner's pinned identity (codex, review of the plan).
"""

from __future__ import annotations

from typing import Any

FIELDS = ("sender_auth", "sender_tenant", "sender_claim_id", "sender_participant_id")
_CLAIM = "pk_claim"  # quorus.auth.participant_routes.CLAIM_CLAIM


def stamp(auth: Any, tenant_id: str) -> dict[str, str]:
    out = {"sender_tenant": tenant_id}
    if auth.is_legacy:
        out["sender_auth"] = "secret"
    elif auth.claims.get(_CLAIM):
        out["sender_auth"] = "key"
        out["sender_claim_id"] = str(auth.claims[_CLAIM])
    elif auth.participant_id:
        out["sender_auth"] = "jwt"
        out["sender_participant_id"] = str(auth.participant_id)
    else:
        out["sender_auth"] = "unbound"
    return out
