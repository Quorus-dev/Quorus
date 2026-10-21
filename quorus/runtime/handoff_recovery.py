"""Explicit owner retries recover interrupted continuations without re-adoption."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime import handoff_runtime as runtime
from quorus.runtime import handoff_sender
from quorus.runtime.checkpoint import run_checkpoint
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_completion import Continuation
from quorus.runtime.handoff_delivery import Decision, quoted
from quorus.runtime.handoff_hold import PublicationHold
from quorus.runtime.handoff_ownership import fenced, owns_claim
from quorus.runtime.handoff_receiver import classify, complete
from quorus.runtime.handoff_state import Duplicate, StateError


class CheckRefusal(StateError):
    """A fixed proof classification, never tool output or exception text."""

    def __init__(self, outcome: str):
        super().__init__("current check does not authorize continuation")
        self.outcome = outcome


@dataclass(frozen=True)
class Candidate:
    turn: Continuation
    head: str
    branch: str
    outcome: str


def _prepare(daemon, room, worktree, thread, bindings):
    store = runtime.journal(daemon, room, worktree)
    # A continuation that itself transferred ownership must not resume, even on
    # another room's ask. Its successor remains the single owner of that tree.
    if fenced(store, Git(worktree.path), worktree.main):
        raise StateError("continuation transferred ownership")
    with store.locked():
        entries = [e for e in store._load().values() if e.get("scope") == store.scope
                   and e["state"] == "continuing" and e.get("root") == thread]
        if len(entries) != 1:
            return None
        entry = entries[0]
        cap = HandoffCapsule.model_validate(entry["snapshot"])
        task_id = entry.get("task_id")
        if not task_id or entry.get("branch") != worktree.branch:
            raise StateError("continuation metadata missing")
        cfg = runtime.config(bindings, room, worktree.repo)
        if (cfg is None or cap.check.status != "ran" or cfg.config_id != cap.check.config_id
                or any(getattr(cap.check, k) != v for k, v in cfg.record().items())):
            raise StateError("owner check changed")
        git = Git(worktree.path)
        head = git.text("rev-parse", "HEAD")
        _tree(git, worktree.branch, head, store, cap)
        rerun = run_checkpoint(worktree.path, head, cfg, env=runtime.check_env(),
                               known_secrets=(daemon.config.api_key,))
        proof = classify(cap.check, rerun)
        if not proof.writable:
            raise CheckRefusal(proof.outcome)
        _tree(Git(worktree.path), worktree.branch, head, store, cap)
        return Candidate(Continuation(cap, store, worktree.path, room, task_id), head,
                         worktree.branch, proof.outcome)


def _tree(git, branch, head, store, cap):
    if (git.text("symbolic-ref", "HEAD") != f"refs/heads/{branch}"
            or git.text("rev-parse", "HEAD") != head or not git.clean()
            or not PublicationHold(git.root, store, cap).owned()):
        raise StateError("interrupted tree or hold changed")
    git.run("merge-base", "--is-ancestor", cap.repo.head, head)


def _authorize(candidate, wake_id):
    turn = candidate.turn
    with turn.journal.locked():
        entry = turn.journal.read(turn.capsule)
        if entry is None or entry["state"] != "continuing":
            raise StateError("continuation state changed")
        retries = entry.get("retry_wakes", [])
        if wake_id in retries:
            raise Duplicate("owner retry already launched")
        if not isinstance(retries, list) or len(retries) >= 16:
            raise StateError("owner retry ledger full")
        _tree(Git(turn.root), candidate.branch, candidate.head, turn.journal, turn.capsule)
        turn.journal.authorize_retry(turn.capsule, wake_id, candidate.head)


async def recover(daemon, relay, envelope, worktree, *, owner_retry, mode, bindings):
    """New trusted OWNER asks on the original thread may resume, duplicates may not.

    No capsule delivery uses this path. The original continuing record and matching
    hold supply identity; the current descendant gets its own bounded local check.
    Fresh relay ownership is checked before that check AND immediately before the
    durable retry authorization. No branch moves or partial commits are discarded.
    """
    if (not handoff_sender.enabled(envelope.get("room") or "", bindings) or not owner_retry
            or worktree is None or mode in {"manual", "readonly"}):
        return None
    room = envelope.get("room") or ""
    threads = {x for x in (envelope.get("reply_to"), envelope.get("thread_root_id"))
               if isinstance(x, str) and x}
    wake_id = envelope.get("message_id") or envelope.get("id")
    if not threads or not wake_id:
        return None
    store = runtime.journal(daemon, room, worktree)
    try:
        def locate():
            with store.locked():
                return [e for e in store._load().values() if e.get("scope") == store.scope
                        and e["state"] in {"continuing", "done"} and e.get("root") in threads]
        entries = await asyncio.to_thread(locate)
        if len(entries) != 1:
            return None
        actor = daemon.config.participant_name
        task_id = entries[0].get("task_id")
        if wake_id in entries[0].get("retry_wakes", []):
            if entries[0]["state"] == "done":
                cap = HandoffCapsule.model_validate(entries[0]["snapshot"])
                claimed = await owns_claim(relay, room=room, participant=actor, task_id=task_id)
                await asyncio.to_thread(complete, worktree.path, store, cap, claimed=claimed)
            return Decision(drop=True)
        if entries[0]["state"] != "continuing":
            return None
        if not await owns_claim(relay, room=room, participant=actor, task_id=task_id):
            raise StateError("owner retry has no matching claim")
        candidate = await asyncio.to_thread(
            _prepare, daemon, room, worktree, entries[0]["root"], bindings)
        if candidate is None:
            raise StateError("continuation changed during recovery")
        await relay.post_reply(
            room=room, from_name=actor, message_type="status", reply_to=wake_id,
            content=f"owner retry: check {candidate.outcome} at {candidate.head[:12]}; "
                    "partial commits preserved; rechecking claim before continuation")
        if not await owns_claim(relay, room=room, participant=actor, task_id=task_id):
            raise StateError("claim changed before retry launch")
        await asyncio.to_thread(_authorize, candidate, wake_id)
        return Decision(True, turn=candidate.turn,
                        context=f"Continue preserved work at {candidate.head}.\n"
                        + quoted(candidate.turn.capsule))
    except Duplicate:
        return Decision(drop=True)
    except CheckRefusal as exc:
        reason = {
            "already_passing": "check now passes; publish or review?",
            "stale": "check failure changed; review before continuing",
        }.get(exc.outcome, "check could not verify the failure; review the check")
        text = f"continuation retry: {reason} Work remains held and read-only."
        daemon._alert_owner(room, f"⚠ {text}")
        await daemon._post_status(relay, room, envelope, text)
        return Decision()
    except Exception:
        daemon._alert_owner(room, "⚠ continuation retry not authorized; work remains held")
        await daemon._post_status(relay, room, envelope,
                                  "continuation retry unverifiable; read-only, work remains held")
        return Decision()
