"""Review gate, slice 1: a room's pinned reviewer and what counts as its GO.

Plan: docs/plans/review-gate.md (Codex GO on a1f25392). In a room with a
reviewer the daemon holds an agent's commits until the reviewer GOs the exact
tip sha. This module only decides; publishing is ``publish_approved``.

The reviewer is pinned by the relay's stamp, never by name: a message sent
with the relay secret (``sender_auth: "secret"``) can carry any name, and a
released name can be claimed again by someone else. A reviewer that joined
with its own participant key (``key`` or ``jwt``) can be pinned and GO, even
on a self-hosted relay that also has a secret.

Fail closed: a reviewers file that can't be read gates every room with a pin
that matches nothing, and anything that looks like a block holds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from quorus.private_files import write_private_text
from quorus.runtime import review_prefs

REVIEWERS_PATH = Path.home() / ".quorus" / "room-reviewers.json"
# Gates the room, matches no stamp: only the owner's override publishes.
UNREADABLE: dict[str, str] = {"name": "", "tenant": "", "claim_id": ""}
MIN_SHA = 7

_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_CLAUSE = re.compile(r"[;.](?:\s+|$)")
_GO = re.compile(r"(✅.*\bGO\b|\bGO on\b|/approve\b)")
# Taking a GO back anywhere on the line: "correction: no GO yet", "GO
# withdrawn", "scratch that".
_RETRACT = re.compile(r"\b(correction|correct(ed|ing) myself|retract\w*|withdr[ae]w\w*|"
                      r"revok\w*|rescind\w*|scratch that|disregard|ignore (that|my|the)|"
                      r"undo|not yet|not a GO|(no|not|n['’]t|never)\s+(a\s+)?GO)\b",
                      re.IGNORECASE)
_BLOCK = re.compile(r"/disagree\s+blocking\b", re.IGNORECASE)
_DISAGREE = re.compile(r"/disagree\b", re.IGNORECASE)
_STATUS = "(quorus)"
# A GO that waits on something isn't a GO yet: "GO on X after CI passes",
# "GO on X? Waiting for tests" (codex, review of 30601459).
_CONDITION = re.compile(r"\b(after|when|whenever|once|until|till|pending|waiting|wait|"
                        r"assuming|provided|providing|as soon as|as long as|so long as|"
                        r"depends?|depending|subject to|blocked on|tbd)\b", re.IGNORECASE)


def _valid(pin: Any) -> bool:
    if not isinstance(pin, dict) or not isinstance(pin.get("name"), str) or not pin["name"]:
        return False
    if not isinstance(pin.get("tenant"), str):
        return False
    claim, pid = pin.get("claim_id"), pin.get("participant_id")
    return (isinstance(claim, str) and bool(claim)) or (isinstance(pid, str) and bool(pid))


def reviewer_for(room: str, path: Path | None = None) -> dict[str, str] | None:
    """The pinned reviewer of *room*, or None when the room has none.

    A file that can't be read, isn't a map, or holds a malformed entry for
    *room* returns :data:`UNREADABLE`: the room stays gated and no stamp
    matches. A room recorded with no pin (``{"name": ..., "unpinned": true}``)
    is gated the same way."""
    try:
        data = json.loads((path or REVIEWERS_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return dict(UNREADABLE)
    if not isinstance(data, dict):
        return dict(UNREADABLE)
    if room not in data:
        return None  # only an absent entry means the room has no gate
    entry = data[room]
    if not _valid(entry):
        name = entry.get("name") if isinstance(entry, dict) else ""
        return {**UNREADABLE, "name": name if isinstance(name, str) else ""}
    out = {"name": entry["name"], "tenant": entry["tenant"]}
    if isinstance(entry.get("claim_id"), str) and entry["claim_id"]:
        out["claim_id"] = entry["claim_id"]
    else:
        out["participant_id"] = entry["participant_id"]
    return out


def pinned(pin: dict[str, str] | None) -> bool:
    """*pin* can authenticate a GO (not :data:`UNREADABLE` or unpinned)."""
    return _valid(pin)


def save_reviewer(room: str, name: str, *, tenant: str = "", claim_id: str = "",
                  participant_id: str = "", path: Path | None = None) -> None:
    """Record *name* as *room*'s reviewer. With neither a claim nor a
    participant id the room is gated but unpinned: owner override only."""
    if not name:
        raise ValueError("reviewer name required")
    entry: dict[str, Any] = {"name": name, "tenant": tenant}
    if claim_id:
        entry["claim_id"] = claim_id
    elif participant_id:
        entry["participant_id"] = participant_id
    else:
        entry["unpinned"] = True
    _write(path, lambda data: data.__setitem__(room, entry))


def clear_reviewer(room: str, path: Path | None = None) -> None:
    _write(path, lambda data: data.pop(room, None))


def _write(path: Path | None, change: Any) -> None:
    target = path or REVIEWERS_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError) as exc:
        # Never paper over a corrupt file: that would silently un-gate rooms.
        raise OSError(f"{target} can't be read; fix or remove it first") from exc
    if not isinstance(data, dict):
        raise OSError(f"{target} isn't a map of rooms; fix or remove it first")
    change(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_private_text(target, json.dumps(data, indent=2, sort_keys=True) + "\n")


def from_reviewer(msg: dict[str, Any], pin: dict[str, str] | None) -> bool:
    """The relay's stamp shows *pin*'s own key sent *msg*, under its name."""
    return (pinned(pin) and (msg.get("from_name") or "") == pin["name"]
            and review_prefs.authenticated(msg, pin))


def _lines(body: str) -> list[list[str]]:
    """The sender's own unquoted, unfenced lines, minus daemon status lines,
    each split into clauses so "GO on X; no regressions" judges "GO on X"."""
    out = []
    for line in review_prefs._lines(body):
        if line.lstrip().startswith(_STATUS):
            continue
        out.append([c.strip() for c in _CLAUSE.split(line) if c.strip()])
    return out


def _pending(clauses: list[str], i: int) -> bool:
    """Clause *i*'s GO is open: its own clause is negated, or anywhere on the
    unquoted line there is a condition, a question, or a retraction of a GO.
    "After CI passes; GO on X" and "GO on X; correction: no GO yet" both stay
    pending (codex, review of 79a256bb), while a plain description after the
    GO ("no regressions") still counts: negation is judged per clause."""
    if review_prefs._NEGATED.search(clauses[i]):
        return True
    line = " ".join(clauses)
    return bool("?" in line or _CONDITION.search(line) or _RETRACT.search(line)
                or review_prefs._UNCERTAIN.search(line))


def _named(clause: str, pending: list[str]) -> set[str]:
    """Full shas from *pending* that a token in *clause* uniquely names."""
    hits = set()
    for token in _SHA.findall(clause.lower()):
        if len(token) < MIN_SHA:
            continue
        match = [full for full in pending if full.startswith(token)]
        if len(match) == 1:
            hits.add(match[0])
    return hits


def verdict(msg: dict[str, Any], pin: dict[str, str] | None,
            pending: list[str]) -> str | None:
    """``"block"``, ``"go"`` or None for one room message.

    *pending* is ``main..tip`` as full shas, tip last. Only the pinned
    reviewer's own stamped messages count. A ``/disagree blocking`` naming
    any pending commit is a block, and wins over a GO in the same message. A
    GO names the tip itself in a clause that says GO, has no negation, no
    condition, no question and no ``/disagree``, and no later clause on its
    line opens with a condition or asks a question."""
    if not pending or not from_reviewer(msg, pin):
        return None
    tip = pending[-1]
    lines = _lines(msg.get("content") or "")
    if any(_BLOCK.search(c) and _named(c, pending) for line in lines for c in line):
        return "block"
    for line in lines:
        for i, clause in enumerate(line):
            if not _GO.search(clause) or tip not in _named(clause, pending):
                continue
            if _DISAGREE.search(clause) or _pending(line, i):
                continue
            return "go"
    return None


def decide(history: Iterable[dict[str, Any]], pin: dict[str, str] | None,
           pending: list[str]) -> tuple[str, str | None]:
    """Fold the room's history into ``("go" | "blocked" | "waiting", msg id)``.

    In time order a block holds until a later GO on the tip; a GO followed by
    a block is blocked."""
    state, ref = "waiting", None
    for msg in sorted(history, key=lambda m: review_prefs._ts(m.get("timestamp"))):
        said = verdict(msg, pin, pending)
        if said == "block":
            state, ref = "blocked", msg.get("id")
        elif said == "go":
            state, ref = "go", msg.get("id")
    return state, ref
