"""Release backport: the helpers the review gate shares with loop B on weekend.

On weekend this module is loop B's review-preference log (shadow). 0.7.x does
not ship loop B; it ships only what ``quorus.runtime.review_gate`` imports from
here, with the same names and behaviour, so the gate's code stays identical to
weekend's. ``authenticated`` is the same function ``quorus.services.sender_auth``
already carries for the owner pin.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from quorus import ledger
from quorus.services.sender_auth import authenticated

__all__ = ["authenticated", "owner_post"]

# Any negation makes a line unusable: "aaaaaaa is not broken" and "do not
# reject aaaaaaa" read as flags to a keyword match (codex, review of 27d5fd30).
_NEGATED = re.compile(r"\b(not|no|never|without|nothing|none|neither|nor|"
                      r"\w+n['’]t|dont|cant|wont|isnt|arent|doesnt|didnt|shouldnt)\b",
                      re.IGNORECASE)
_FENCE = re.compile(r"^\s*(```|~~~)")
# Questions and conditionals aren't requests or verdicts.
_UNCERTAIN = re.compile(r"\b(if|unless|whether|maybe|might|in case|should (we|i)|"
                        r"could (we|i|you)|would)\b", re.IGNORECASE)
_OVERSEER = re.compile(r"[—–-]\s*overseer\b", re.IGNORECASE)


def owner_post(msg: dict[str, Any], owner: str,
               pin: dict[str, str] | None = None) -> str | None:
    """Why *msg* is not the owner's own word, or None when it is."""
    if (msg.get("from_name") or "") != owner:
        return "not_owner"
    body = msg.get("content") or ""
    if _OVERSEER.search(body):
        return "overseer"
    if re.match(rf"\s*{re.escape(owner)}\s*:", body, re.IGNORECASE):
        return "relay"  # the overseer relaying the owner ("Arav: ...")
    if not authenticated(msg, pin):
        return "unauthenticated"
    return None


def _lines(body: str) -> list[str]:
    """The owner's own unquoted lines, without anything inside a fenced
    block (pasted output, not the owner speaking)."""
    out, fenced = [], False
    for ln in ledger._own_words(body).splitlines():
        if _FENCE.match(ln):
            fenced = not fenced
            continue
        if ln.strip() and not fenced:
            out.append(ln)
    return out


def _ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
