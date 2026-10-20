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


def owners_agent(msg: dict[str, Any], owner: str, pin: dict[str, str]) -> bool:
    """*msg* was sent by one of the pinned owner's own agents: a ``<owner>-*``
    name, stamped ``key`` in the pinned tenant with the owner's claim as its
    owner (``sender_owner_claim_id``). A secret caller can pick a ``bob-*`` name
    but never that stamp (codex, review of 0f9a90b7)."""
    return ((msg.get("from_name") or "").startswith(f"{owner}-")
            and msg.get("sender_tenant") == pin.get("tenant")
            and msg.get("sender_auth") == "key" and bool(msg.get("sender_claim_id"))
            and bool(pin.get("claim_id"))
            and msg.get("sender_owner_claim_id") == pin["claim_id"])


def owner_root(envelope: dict[str, Any], history: list[dict[str, Any]],
               owner: str, pin: dict[str, str]) -> dict[str, Any] | None:
    """The stored message of the owner's request this wake serves: the envelope
    itself when the owner sent it, else the first human message up its reply
    chain, where the triggering message and every hop is one of the owner's
    own agents by the relay's stamp. Stamps live on stored messages only, so a
    message missing from *history* is None (read-only)."""
    by_id = {m.get("id"): m for m in history if m.get("id")}
    sender = envelope.get("from_name") or ""
    # A delivered copy has its own id; the stored message's is message_id.
    msg = by_id.get(envelope.get("message_id") or envelope.get("id"))
    if msg is None or (msg.get("from_name") or "") != sender:
        return None
    seen: set[str] = set()
    while True:
        who = msg.get("from_name") or ""
        if who == owner:
            return msg
        if not owners_agent(msg, owner, pin):
            return None
        seen.add(msg["id"])
        cur = msg.get("reply_to")
        if not cur or cur in seen or cur not in by_id:
            return None
        msg = by_id[cur]


def pinned_owner_request(envelope: dict[str, Any], history: list[dict[str, Any]],
                         owner: str, pin: dict[str, str]) -> bool:
    """This wake serves a request the pinned owner sent with their own key."""
    root = owner_root(envelope, history, owner, pin)
    return root is not None and authenticated(root, pin)
