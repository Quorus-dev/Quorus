"""An invitee's agent trusts its owner's key, not their name
(docs/internal/bets/invitee-agents.md, Codex's trust call on 3212878c).

On a teammate's shared-secret relay the secret's holder can post as anyone, so
a message from "bob" proves nothing. When bob adds an agent with his own
invite key, `quorus agent add` pins his claim: the relay's stamp
(``quorus.services.sender_auth``) must show bob's own participant key for a turn
to be write-capable. Anyone else, the relay owner posting as "bob" included,
gets a read-only turn. The relay operator is still trusted to stamp honestly;
owner signatures (``owner_sig``) remove that trust.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quorus.services.sender_auth import authenticated

PINS_PATH = Path.home() / ".quorus" / "agent-owner-pins.json"
UNREADABLE = {"tenant": "", "claim_id": ""}  # matches no stamp: read-only


def owner_pin(participant: str, path: Path | None = None) -> dict[str, str] | None:
    """The pinned owner identity for *participant*, or None when it has none. A
    file that can't be read or isn't a map of pins pins everyone to nothing."""
    try:
        data = json.loads((path or PINS_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return UNREADABLE
    if not isinstance(data, dict):
        return UNREADABLE
    pin = data.get(participant)
    if pin is None:
        return None
    if (not isinstance(pin, dict) or not isinstance(pin.get("tenant"), str)
            or not isinstance(pin.get("claim_id"), str) or not pin["claim_id"]):
        return UNREADABLE
    return {"tenant": pin["tenant"], "claim_id": pin["claim_id"]}


def save_pin(participant: str, tenant: str, claim_id: str, path: Path | None = None) -> None:
    from quorus.private_files import write_private_text

    target = path or PINS_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[participant] = {"tenant": tenant, "claim_id": claim_id}
    target.parent.mkdir(parents=True, exist_ok=True)
    write_private_text(target, json.dumps(data, indent=2) + "\n")


def owner_root(envelope: dict[str, Any], history: list[dict[str, Any]],
               owner: str) -> dict[str, Any] | None:
    """The stored message of the owner's request this wake serves: the envelope
    itself when the owner sent it, else the first human message up its reply
    chain through the owner's agents. Stamps live on stored messages only, so a
    message missing from *history* is None (read-only)."""
    by_id = {m.get("id"): m for m in history if m.get("id")}
    sender = envelope.get("from_name") or ""
    if sender == owner:
        # A delivered copy has its own id; the stored message's is message_id.
        return by_id.get(envelope.get("message_id") or envelope.get("id"))
    if not sender.startswith(f"{owner}-"):
        return None
    cur, seen = envelope.get("reply_to"), set()
    while cur and cur in by_id and cur not in seen:
        seen.add(cur)
        msg = by_id[cur]
        who = msg.get("from_name") or ""
        if who == owner:
            return msg
        if not who.startswith(f"{owner}-"):
            return None
        cur = msg.get("reply_to")
    return None


def pinned_owner_request(envelope: dict[str, Any], history: list[dict[str, Any]],
                         owner: str, pin: dict[str, str]) -> bool:
    """This wake serves a request the pinned owner sent with their own key."""
    root = owner_root(envelope, history, owner)
    return root is not None and authenticated(root, pin)
