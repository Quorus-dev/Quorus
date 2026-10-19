"""Hold a turn's commits when the owner or lead wrote to the agent mid-turn.

On 2026-10-18 a stand-down ("please stand down on slice 1") reached an agent
while it was building slice 1. The message was queued as its next wake, but
the turn ended first and the daemon published the work, so slice 1 was built
twice. Now, before anything is published, the daemon reads the room from the
start of the oldest unpublished turn. If the owner or the room's lead
@-mentioned the agent in that window, or that history can't be read in full,
the commits go to a fresh ``<branch>-held-<stamp>`` ref instead and the agent's
branch goes back to main. Nothing is lost and nothing is published.

The next wake is told which commits were held and why. It must answer with
``reapply held`` (the daemon cherry-picks them back) or ``drop held``; a turn
that does neither gets a ⚠ in the room. Agents never need reset or rebase.

The window starts at the oldest unpublished turn, not the latest wake: a
conflict-resolution wake that saw no new messages must not publish what an
earlier turn should have held (claude, review of the plan). Markers live in the
daemon's runtime dir, so a restart keeps every hold.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REAPPLY = "reapply held"
DROP = "drop held"
HISTORY_LIMIT = 200
_EXCERPT = 300


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=path,
                          capture_output=True, text=True, timeout=120)


def _file(held_dir: Path, path: Path, suffix: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(Path(path).resolve()))[-150:]
    return held_dir / f"{safe}.{suffix}"


def _write_json(target: Path, data: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".held-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- the window: when the oldest unpublished turn started ---------------------


_SEEN_MAX = 500


def turn_start(history: list[dict[str, Any]], envelope: dict[str, Any]) -> float:
    """When a turn started, on the relay's clock: the newest message it had
    in view. Comparing relay timestamps with the daemon's own clock made a
    relay running ahead count the turn's own trigger as mid-turn (claude,
    review of 3ba308a4)."""
    stamps = [_epoch(m.get("timestamp")) for m in [*history, envelope]]
    known = [s for s in stamps if s is not None]
    return max(known) if known else time.time()


def mark_window(held_dir: Path, wt, started_at: float | None = None,
                seen: set[str] | list[str] = ()) -> None:
    """Called as a writable turn starts. The first unpublished turn opens the
    window: its start and the ids it had in view (never mid-turn for it). Later
    turns change neither. Adding their context let a stand-down that a later
    wake happened to see excuse the older turn's commits (codex, review of
    3ba308a4)."""
    if _read_window(held_dir, wt) is not None:
        return
    ids = list(dict.fromkeys(i for i in seen if i))
    _write_json(_file(held_dir, wt.path, "window"),
                {"since": time.time() if started_at is None else started_at,
                 "seen": ids[-_SEEN_MAX:]})


def _read_window(held_dir: Path, wt) -> dict[str, Any] | None:
    try:
        data = json.loads(_file(held_dir, wt.path, "window").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def window(held_dir: Path, wt) -> float | None:
    data = _read_window(held_dir, wt)
    try:
        return float(data["since"]) if data else None
    except (KeyError, TypeError, ValueError):
        return None


def seen_ids(held_dir: Path, wt) -> set[str]:
    data = _read_window(held_dir, wt) or {}
    return {i for i in data.get("seen", []) if isinstance(i, str)}


def _ahead(wt) -> list[str]:
    out = _git(wt.repo, "rev-list", "--reverse", f"{wt.main}..{wt.branch}")
    if out.returncode != 0:
        raise OSError(f"couldn't compare {wt.branch} with {wt.main}")
    return [s for s in out.stdout.split() if s]


def settle_window(held_dir: Path, wt) -> None:
    """Forget the window once nothing is left unpublished on the branch."""
    if not _ahead(wt):
        _file(held_dir, wt.path, "window").unlink(missing_ok=True)


def oldest_commit_time(wt) -> float | None:
    """Fallback window for commits made before this guard existed."""
    shas = _ahead(wt)
    if not shas:
        return None
    out = _git(wt.repo, "show", "-s", "--format=%ct", shas[0])
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


# --- the trigger: a mid-turn message from the owner or the lead ---------------


def _epoch(stamp: Any) -> float | None:
    if isinstance(stamp, (int, float)):
        return float(stamp)
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _mentions(content: str, me: str) -> bool:
    return re.search(rf"(?<![\w-])@{re.escape(me)}(?![\w-])", content or "") is not None


def find_stop(history: list[dict[str, Any]], *, me: str, authorities: set[str],
              since: float, seen: set[str] | None = None,
              limit: int = HISTORY_LIMIT) -> tuple[dict[str, Any] | None, str]:
    """``(trigger, why)``. The trigger is the first message after *since* that
    an authority (the owner or the room's lead, as authenticated by the relay)
    wrote and that @-mentions *me*. With no trigger, *why* is non-empty when
    the history can't prove there was none (then the caller holds too)."""
    stamps = [_epoch(m.get("timestamp")) for m in history]
    known = [s for s in stamps if s is not None]
    if len(history) >= limit and (not known or min(known) > since):
        return None, "the room's history since the turn started couldn't be read in full"
    for msg, stamp in sorted(zip(history, stamps), key=lambda p: p[1] or 0.0):
        if stamp is None or stamp <= since or (msg.get("id") in (seen or ())):
            continue
        sender = msg.get("from_name") or msg.get("from") or ""
        if sender == me or sender not in authorities:
            continue
        if _mentions(msg.get("content") or "", me):
            return msg, ""
    return None, ""


# --- holding, telling the next wake, and resolving -----------------------------


def _entries(held_dir: Path, wt) -> list[dict[str, Any]]:
    try:
        data = json.loads(_file(held_dir, wt.path, "json").read_text())
    except (OSError, ValueError):
        return []
    return data.get("entries", []) if isinstance(data, dict) else []


def _save(held_dir: Path, wt, entries: list[dict[str, Any]]) -> None:
    target = _file(held_dir, wt.path, "json")
    if entries:
        _write_json(target, {"entries": entries})
    else:
        target.unlink(missing_ok=True)


def pending(held_dir: Path, wt) -> bool:
    return bool(_entries(held_dir, wt))


def hold(held_dir: Path, wt, participant: str, *, trigger: dict[str, Any] | None,
         why: str) -> str | None:
    """Move the branch's unpublished commits to a fresh held ref. Returns the
    ref, or None when there was nothing unpublished to hold. The branch is
    moved back to main only when the worktree is clean and no merge is in
    progress; otherwise the commits stay put, the entry is marked unmoved and
    :func:`blocks_publish` keeps them from being published."""
    from reflexd_wake import commit_all, merge_in_progress, worktree_dirty

    merging = merge_in_progress(wt)
    if not merging:
        commit_all(wt, participant, f"wip: held turn by {participant}")
    shas = _ahead(wt)
    if not shas:
        return None
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    ref = f"{wt.branch}-held-{stamp}-{secrets.token_hex(2)}"
    if _git(wt.repo, "branch", ref, wt.branch).returncode != 0:
        raise OSError(f"couldn't create {ref}")
    moved = False
    if not merging and not worktree_dirty(wt):
        moved = _git(wt.path, "reset", "--quiet", "--hard", wt.main).returncode == 0
    entries = _entries(held_dir, wt)
    entries.append({
        "ref": ref, "shas": shas, "moved": moved, "shown": False, "at": time.time(),
        "why": why,
        "trigger": None if trigger is None else {
            "from": trigger.get("from_name") or trigger.get("from") or "",
            "id": trigger.get("id") or "",
            "excerpt": (trigger.get("content") or "")[:_EXCERPT],
        },
    })
    _save(held_dir, wt, entries)
    return ref


def blocks_publish(held_dir: Path, wt) -> bool:
    """An unmoved hold whose commits are still on the branch blocks
    publishing. Retries the move first (a merge may have finished)."""
    from reflexd_wake import merge_in_progress, worktree_dirty

    entries = _entries(held_dir, wt)
    blocking = False
    for entry in entries:
        if entry.get("moved"):
            continue
        if not merge_in_progress(wt) and not worktree_dirty(wt):
            entry["moved"] = _git(wt.path, "reset", "--quiet", "--hard",
                                  wt.main).returncode == 0
        blocking = blocking or not entry["moved"]
    _save(held_dir, wt, entries)
    return blocking


def status(ref: str, shas: list[str], trigger: dict[str, Any] | None, why: str) -> str:
    who = (f"@{trigger.get('from_name') or trigger.get('from')} wrote to me mid-turn"
           if trigger else why)
    return (f"⚠ held {len(shas)} commit(s) instead of publishing: {who}. They are on "
            f"{ref}. My next turn reapplies or drops them.")


def prompt_note(held_dir: Path, wt) -> str:
    """The next wake's instructions for every held, not yet shown, entry."""
    entries = _entries(held_dir, wt)
    lines: list[str] = []
    for entry in entries:
        if not entry.get("moved"):
            continue
        trig = entry.get("trigger") or {}
        reason = (f"@{trig.get('from')} wrote to you while you were working: "
                  f"\"{trig.get('excerpt', '')}\"" if trig else entry.get("why", ""))
        lines.append(f"- {entry['ref']} ({', '.join(s[:8] for s in entry['shas'])}): {reason}")
        entry["shown"] = True
    _save(held_dir, wt, entries)
    if not lines:
        return ""
    return ("HELD COMMITS: your previous turn's commits were NOT published, because "
            "someone redirected you mid-turn. Read that message first.\n" + "\n".join(lines) +
            f"\nIf the work still applies, put `{REAPPLY}` on a line of its own in your "
            f"reply and the daemon cherry-picks it back. If it doesn't, put `{DROP}` on a "
            "line of its own. Exactly one of the two.")


def answer(reply: str) -> str | None:
    """The reply's answer: REAPPLY, DROP, or None. It must stand on a line of
    its own (backticks and a trailing period allowed): "I won't reapply held
    commits ... drop held" reapplied when any substring counted (claude,
    review of 3ba308a4). A reply carrying both is no answer."""
    lines = {line.strip().strip("`*_ .!").lower()
             for line in (reply or "").splitlines()}
    found = {phrase for phrase in (REAPPLY, DROP) if phrase in lines}
    return found.pop() if len(found) == 1 else None


def resolve(held_dir: Path, wt, reply: str, participant: str) -> list[str]:
    """After a writable turn that was shown the note: apply its answer.
    Returns room lines to post (a ⚠ when it didn't answer, or the reapply
    failed). Shown entries are always settled; their refs stay for humans."""
    said = answer(reply)
    entries = _entries(held_dir, wt)
    keep: list[dict[str, Any]] = []
    out: list[str] = []
    for entry in entries:
        if not entry.get("shown"):
            keep.append(entry)
            continue
        ref, shas = entry["ref"], entry["shas"]
        if said == REAPPLY:
            picked = _git(wt.path, "-c", f"user.name={participant}", "-c",
                          f"user.email={participant}@agents.quorus.local",
                          "cherry-pick", "--allow-empty", *shas)
            if picked.returncode != 0:
                _git(wt.path, "cherry-pick", "--abort")
                out.append(f"⚠ couldn't reapply {ref} cleanly; it stays there for a human")
        elif said != DROP:
            out.append(f"⚠ held commits on {ref} were neither reapplied nor dropped; "
                       "they stay there, unpublished")
    _save(held_dir, wt, keep)
    return out
