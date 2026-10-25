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

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from quorus.private_files import write_private_text
from quorus.runtime import review_prefs


def reviewers_path() -> Path:
    """Resolved per call, so a test's HOME (and QUORUS_CONFIG_DIR) is honored."""
    from quorus.runtime import bindings
    return bindings.config_dir() / "room-reviewers.json"
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
        data = json.loads((path or reviewers_path()).read_text(encoding="utf-8"))
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


class _AllRooms(frozenset):
    """Every room may be reviewed: the reviewers file can't be read."""


ALL_ROOMS = _AllRooms()


def reviewed_rooms(path: Path | None = None) -> frozenset[str]:
    """Rooms with a reviewer entry (well-formed or not). :data:`ALL_ROOMS`
    when the file can't be read, so callers fail closed."""
    try:
        data = json.loads((path or reviewers_path()).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return frozenset()
    except (OSError, json.JSONDecodeError):
        return ALL_ROOMS
    if not isinstance(data, dict):
        return ALL_ROOMS
    return frozenset(str(k) for k in data)


def pinned(pin: dict[str, str] | None) -> bool:
    """*pin* can authenticate a GO (not :data:`UNREADABLE` or unpinned)."""
    return _valid(pin)


def save_reviewer(room: str, name: str, *, tenant: str = "", claim_id: str = "",
                  participant_id: str = "", path: Path | None = None,
                  repo: Path | str | None = None) -> None:
    """Record *name* as *room*'s reviewer. With neither a claim nor a
    participant id the room is gated but unpinned: owner override only. *repo*
    records the repo *room* gates, so a daemon whose own bindings name only a
    sibling room on that repo still finds the gate (codex, review of aa965c81)."""
    if not name:
        raise ValueError("reviewer name required")
    entry: dict[str, Any] = {"name": name, "tenant": tenant}
    if repo:
        entry["repo"] = str(Path(repo).expanduser().resolve())
    if claim_id:
        entry["claim_id"] = claim_id
    elif participant_id:
        entry["participant_id"] = participant_id
    else:
        entry["unpinned"] = True
    _write(path, lambda data: data.__setitem__(room, entry))


def reviewer_repo(room: str, path: Path | None = None) -> Path | None:
    """The repo *room*'s reviewer entry says it gates, or None."""
    try:
        data = json.loads((path or reviewers_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = data.get(room) if isinstance(data, dict) else None
    raw = entry.get("repo") if isinstance(entry, dict) else None
    return Path(raw) if isinstance(raw, str) and raw else None


def clear_reviewer(room: str, path: Path | None = None) -> None:
    _write(path, lambda data: data.pop(room, None))


def _write(path: Path | None, change: Any) -> None:
    target = path or reviewers_path()
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
    """``"block"``, ``"go"``, ``"retract"`` or None for one room message.

    *pending* is ``main..tip`` as full shas, tip last. Only the pinned
    reviewer's own stamped messages count. A ``/disagree blocking`` naming
    any pending commit is a block, and wins over a GO in the same message. A
    GO names the tip itself in a clause that says GO, has no negation, no
    condition, no question and no ``/disagree``, and no later clause on its
    line opens with a condition or asks a question. A line naming the tip
    that retracts a GO, or a GO that isn't current (conditional, negated, a
    question), is ``"retract"``: it cancels an earlier GO."""
    if not pending or not from_reviewer(msg, pin):
        return None
    tip = pending[-1]
    lines = _lines(msg.get("content") or "")
    if any(_BLOCK.search(c) and _named(c, pending) for line in lines for c in line):
        return "block"
    # The whole message first: a retraction or open condition on ANY own line
    # that names the tip, or names no commit at all, outranks a GO on another
    # line ("GO on X\nCorrection: no GO yet on X": codex, review of b116d1a2).
    gos, retracted = 0, False
    for line in lines:
        text = " ".join(line)
        shas = {t for t in _SHA.findall(text.lower()) if len(t) >= MIN_SHA}
        names_tip = any(tip in _named(c, pending) for c in line)
        if (names_tip or not shas) and (_RETRACT.search(text) or (
                line and (_CONDITION.match(line[0]) or review_prefs._UNCERTAIN.match(line[0])))):
            retracted = True
        for i, clause in enumerate(line):
            if not _GO.search(clause) or tip not in _named(clause, pending):
                continue
            if _DISAGREE.search(clause) or _pending(line, i):
                retracted = True  # a conditional or taken-back GO: not current
            else:
                gos += 1
    if retracted:
        return "retract"
    return "go" if gos else None


def decide(history: Iterable[dict[str, Any]], pin: dict[str, str] | None,
           pending: list[str]) -> tuple[str, str | None]:
    """Fold the room's history into ``("go" | "blocked" | "waiting", msg id)``.

    In time order a block holds until a later GO on the tip; a GO followed by
    a block is blocked; a GO followed by a retraction is waiting again."""
    state, ref = "waiting", None
    for msg in sorted(history, key=lambda m: review_prefs._ts(m.get("timestamp"))):
        said = verdict(msg, pin, pending)
        if said == "block":
            state, ref = "blocked", msg.get("id")
        elif said == "go":
            state, ref = "go", msg.get("id")
        elif said == "retract" and state == "go":
            state, ref = "waiting", None
    return state, ref


# ── pending-review markers (slice 3) ─────────────────────────────────────


def marker_path(runtime_dir: Path, repo: Path, branch: str) -> Path:
    """One marker per checkout and branch, named by a hash of both (never a
    sanitized path: codex, review of eed52d75)."""
    key = f"{os.path.realpath(repo)}\0{branch}".encode("utf-8")
    return runtime_dir / "review-gate" / f"{hashlib.sha256(key).hexdigest()}.json"


def save_marker(runtime_dir: Path, repo: Path, branch: str, *, room: str, tip: str,
                base: str, count: int, reviewer: str, now: float) -> dict[str, Any]:
    """Record that *tip* waits for *reviewer*. A marker for the same tip keeps
    its first time, so the review timeout isn't reset by a re-hold."""
    prior = load_marker(runtime_dir, repo, branch)
    since = prior["since"] if prior and prior.get("tip") == tip else now
    data = {"repo": os.path.realpath(repo), "branch": branch, "room": room, "tip": tip,
            "base": base, "count": count, "reviewer": reviewer, "since": since}
    path = marker_path(runtime_dir, repo, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private_text(path, json.dumps(data, sort_keys=True) + "\n")
    return data


def load_marker(runtime_dir: Path, repo: Path, branch: str) -> dict[str, Any] | None:
    """The pending review for *branch*, None when there is none. An unreadable
    marker reads as one that names no tip: nothing it says can publish."""
    try:
        data = json.loads(marker_path(runtime_dir, repo, branch).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"tip": "", "unreadable": True}
    if not isinstance(data, dict) or data.get("repo") != os.path.realpath(repo):
        return {"tip": "", "unreadable": True}
    return data


def clear_marker(runtime_dir: Path, repo: Path, branch: str) -> None:
    try:
        marker_path(runtime_dir, repo, branch).unlink()
    except FileNotFoundError:
        pass


def self_review(pin: dict[str, str] | None, me: str) -> bool:
    """*me* is the room's reviewer: its own GO never publishes its own commits
    (codex, 2026-10-25). Those wait for the owner's override."""
    return bool(pin) and bool(me) and pin.get("name") == me


def awaiting_status(pin: dict[str, str], tip: str, count: int, me: str = "") -> str:
    """The room status for a held tip: honest about what is (not) on main."""
    who = pin.get("name") or "the reviewer"
    if self_review(pin, me):
        return (f"{_STATUS} {tip[:12]} ({count} commit(s)) is held: @{who} is this room's "
                "reviewer and can't GO its own commits; owner override only. Not on main.")
    if pin.get("why") == "bindings":
        return (f"{_STATUS} {tip[:12]} ({count} commit(s)) is held: this repo may have a "
                "reviewer, but the room bindings can't be read to tell; owner override "
                "only. Not on main.")
    if not pinned(pin):
        return (f"{_STATUS} {tip[:12]} ({count} commit(s)) is held: review by @{who} can't "
                "be authenticated on this relay; owner override only. Not on main.")
    # Say how to approve: a first-run reviewer was never told, so a "/vote approve"
    # or "LGTM" held the fix forever (gate-by-default slice C).
    return (f"{_STATUS} awaiting @{who}'s review of {tip[:12]} ({count} commit(s)); not on "
            f"main. @{who}: reply `✅ GO on {tip[:12]}` to publish it, or "
            f"`/disagree blocking` with why.")


def update_marker(runtime_dir: Path, repo: Path, branch: str, **fields: Any) -> None:
    """Merge *fields* into an existing, readable marker (no-op otherwise)."""
    data = load_marker(runtime_dir, repo, branch)
    if not data or data.get("unreadable"):
        return
    data.update(fields)
    write_private_text(marker_path(runtime_dir, repo, branch),
                       json.dumps(data, sort_keys=True) + "\n")


def reviewed_note(n: int, branch: str, main: str, suffix: str, reviewer: str,
                  msg_id: str | None) -> str:
    """The usual publish note (its " at <head> (author: …)" *suffix* keeps the
    ledger and brief parsers working), plus who reviewed it."""
    ref = f" (GO {msg_id})" if msg_id else ""
    return (f"{_STATUS} published {n} commit(s) from {branch} to {main}{suffix}; "
            f"reviewed by @{reviewer}{ref}")


# ── slice 3c: timeout and the owner's override ───────────────────────────

DEFAULT_TIMEOUT_S = 20 * 60
_OVERRIDE = re.compile(r"^\s*(?:@[\w-]+[,:]?\s+)*publish\s+`?([0-9a-f]{7,40})`?\s+unreviewed\s*$",
                       re.IGNORECASE)


def timeout_s() -> int:
    """How long a held tip waits before the one ⚠ (``QUORUS_REVIEW_TIMEOUT_S``)."""
    try:
        value = int(os.environ.get("QUORUS_REVIEW_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def owner_override(history: Iterable[dict[str, Any]], owner: str,
                   owner_pin: dict[str, str] | None, tip: str) -> str | None:
    """The id of the owner's own ``publish <sha> unreviewed`` for exactly *tip*.

    The whole line must be the command (after @-mentions), naming a prefix
    of *tip*. Only the owner's own words count, by the same check as loop B:
    their pinned stamp, never a message signed "— overseer", relayed, quoted
    or fenced (review_prefs.owner_post). No pin: no in-room override."""
    found = None
    for msg in sorted(history, key=lambda m: review_prefs._ts(m.get("timestamp"))):
        if review_prefs.owner_post(msg, owner, owner_pin) is not None:
            continue
        for line in review_prefs._lines(msg.get("content") or ""):
            m = _OVERRIDE.match(line)
            if m and len(m.group(1)) >= MIN_SHA and tip.startswith(m.group(1).lower()):
                found = msg.get("id") or "?"
    return found


def timeout_note(pin: dict[str, str], tip: str, waited_s: float, owner: str,
                 in_room: bool) -> str:
    who = pin.get("name") or "the reviewer"
    how = (f"@{owner} can say `publish {tip[:12]} unreviewed`" if in_room
           else f"@{owner} can run `quorus review publish {tip[:12]} --unreviewed` "
                "on this machine")
    return (f"{_STATUS} ⚠ no review of {tip[:12]} from @{who} after "
            f"{int(waited_s // 60)} min; still not on main. {how}.")


def override_note(n: int, branch: str, main: str, suffix: str, owner: str,
                  msg_id: str | None) -> str:
    ref = f" {msg_id}" if msg_id else ""
    return (f"{_STATUS} published {n} commit(s) from {branch} to {main}{suffix}; "
            f"unreviewed (owner override by @{owner}{ref})")
