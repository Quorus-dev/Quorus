"""Checkpoint, durable starter fence and bounded busy-receiver retries.

Live handoff is off unless the owner switched it on for that room (`quorus room
bind <room> <path> --handoff on`, dogfood plan §1). There is no global switch
and no environment override.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from quorus.protocol import SocialVerb
from quorus.protocol.handoff_capsule import HandoffCapsule, LimitContext
from quorus.runtime import failover
from quorus.runtime import handoff_runtime as runtime
from quorus.runtime import handoff_wait as waits
from quorus.runtime.checkpoint import run_checkpoint
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_capsule import build_capsule
from quorus.runtime.handoff_ownership import owns_claim
from quorus.runtime.handoff_state import fingerprint


def enabled(room: str, bindings) -> bool:
    """Is live handoff on for *room*? The owner's per-room flag, read every call."""
    return runtime.handoff_enabled(bindings, room)


async def select_target(relay, *, room, actor, task_id, failed_agents, task,
                        detect, capabilities, score, bindings, event):
    """Busy is temporary unavailability, not a permanent candidate exclusion.

    Do not send to a busy receiver: it returns a typed decline and is excluded
    from this capsule's retries. In a two-agent room that leaves no receiver.
    Keep the original claim while polling, then use the ordinary proof path.
    """
    policy = dict(actor=actor, failed=failed_agents, task=task, detect=detect,
                  capabilities=capabilities, score=score)
    if not enabled(room, bindings):
        return None, "gate_closed"
    if not await owns_claim(relay, room=room, participant=actor, task_id=task_id):
        return None, "no_claim"
    entries = await relay.fetch_presence()
    members = await relay.fetch_members(room=room)
    target = failover.choose_candidate(entries, members, **policy)
    busy = failover.choose_candidate(entries, members, statuses=("busy",), **policy)
    event("selection", target=target, busy_target=busy,
          active=[e["name"] for e in entries if e.get("name") in members
                  and e.get("online") and e.get("status") == "active"],
          busy=[e["name"] for e in entries if e.get("name") in members
                and e.get("online") and e.get("status") == "busy"])
    return (target, None) if target else (busy, "busy" if busy else "no_target")


def snapshot(daemon, room, worktree, data, bindings):
    git = Git(worktree.path)
    head = git.text("rev-parse", "HEAD")
    cfg = runtime.config(bindings, room, worktree.repo)
    reading = daemon.adapter.current_limit()
    if reading is None:
        limit = {"vendor": data["vendor"], "reason": data["reason"], "kind": "guess",
                 "source": "msg.no_time", "seen_at": datetime.now(timezone.utc).isoformat()}
    else:
        limit = LimitContext.from_presence(
            asdict(reading), vendor=data["vendor"], reason=data["reason"]).model_dump()
    record = run_checkpoint(worktree.path, head, cfg, env=runtime.check_env(),
                            known_secrets=(daemon.config.api_key,))
    return build_capsule(
        root=data["original_message_id"], hop=data["handoffs"],
        actor=daemon.config.participant_name, task=data["task"],
        repo={"main": worktree.main, "base": git.text("rev-parse", worktree.main),
              "branch": worktree.branch, "head": head,
              "tree": git.text("rev-parse", f"{head}^{{tree}}"),
              "unfinished": worktree.branch + "-unfinished"},
        checkpoint=record, config=cfg, limit=limit,
        spent=data.get("spent") or {"turns": 1, "wall_s": record.get("wall_s", 0.0),
                                    "attempts": 1},
        known_secrets=(daemon.config.api_key,))


async def _send(daemon, relay, room, store, capsule, data, target, declines):
    actor = daemon.config.participant_name
    if not await owns_claim(relay, room=room, participant=actor, task_id=data["task_id"]):
        raise ValueError("starter no longer owns claim")
    def persist():
        with store.locked():
            store.write(capsule, "sent", snapshot=capsule.model_dump(), data=data,
                        target=target, declines=declines)
    await asyncio.to_thread(persist)  # fence BEFORE either external side effect
    await relay.release_for_failover(room=room, actor=actor, target=target,
                                     task_id=data["task_id"])
    await relay.post_social_defer(room_id=room, actor=actor, target=target,
                                  ref_message_id=capsule.root, failover=data)
    try:
        from quorus.runtime import task_capture, task_record
        from quorus.services.approval_svc import agent_owner

        await asyncio.to_thread(task_capture.escalation, store.repo,
            task_record.scope(daemon.config.relay_url, agent_owner(actor), store.repo),
            capsule.root, target)
    except Exception:
        pass  # the transfer already succeeded; display evidence never changes it


async def attempt(daemon, relay, envelope, *, reason, mission, worktree,
                  detect, capabilities, score, bindings=None, spent=None):
    if worktree is None:
        return "No isolated handoff workspace; owner needs to reassign."
    room, actor = envelope.get("room") or "", daemon.config.participant_name
    # Durable attempt events (dogfood plan §1): written before the checkpoint runs, so
    # an attempt that never reaches a capsule is still counted. Causes are fixed
    # words, never exception text.
    attempt_id, stage = uuid.uuid4().hex, "attempt"

    def event(kind: str, **fields) -> None:
        store.event({"event": kind, "attempt_id": attempt_id, "agent": actor, "room": room,
                     "at": datetime.now(timezone.utc).isoformat(), **fields})

    def failed(cause: str, answer: str) -> str:
        event("attempt_failed", cause=cause)
        waits.alert(daemon, room, cause)
        return "stopped"  # no ambient chat invitation to take over unverified work

    try:
        store = runtime.journal(daemon, room, worktree)
        event("attempt", reason=reason)
    except Exception:
        daemon._alert_owner(room, "⚠ handoff did not complete; work kept unpublished")
        return "stopped"
    previous = failover.continuation(envelope) or {}
    original = previous.get("root") or envelope.get("message_id") or envelope.get("id")
    failed_agents = list(dict.fromkeys([*previous.get("failed_agents", []), actor]))
    if (not original or previous.get("handoffs", 0) >= 2
            or actor in previous.get("failed_agents", [])):
        daemon._alert_owner(room, "⚠ stuck: handoff limit reached; needs you")
        return failed("hop_limit", "Handoff limit reached; owner needed.")
    task_id = (mission or {}).get("task_id") or previous.get("task_id")
    try:
        stage = "claim"
        if not await owns_claim(relay, room=room, participant=actor, task_id=task_id):
            return failed("no_claim", "No owned claim; owner needs to reassign.")
        task = " ".join(str((mission or {}).get("summary") or previous.get("task")
                            or envelope.get("content") or "Continue task").split())[:120]
        stage = "target"
        target, cause = await select_target(
            relay, room=room, actor=actor, task_id=task_id, failed_agents=failed_agents,
            task=task, detect=detect, capabilities=capabilities, score=score,
            bindings=bindings, event=event)
        if not target:
            return failed(cause, "No available handoff receiver; owner needed.")
        budget = (await waits.notice(daemon, relay, envelope, target, bindings)
                  if cause == "busy" else None)
        data = {"original_message_id": original, "task_id": task_id, "task": task,
                "reason": reason, "branch": worktree.branch, "last_commit": "",
                "failed_agents": failed_agents, "handoffs": previous.get("handoffs", 0) + 1}
        stage = "checkpoint"
        capsule = await asyncio.to_thread(snapshot, daemon, room, worktree,
                                           {**data, "vendor": detect(actor), "spent": spent},
                                           bindings)
        check = capsule.check
        event("capsule_built", target=target, capsule_id=capsule.id,
              fingerprint=fingerprint(capsule),
              capsule_bytes=len(capsule.model_dump_json().encode()),
              seen_at=capsule.limit.seen_at, limit_kind=capsule.limit.kind,
              check={"argv": list(getattr(check, "argv", None) or []),
                     "config_id": getattr(check, "config_id", None),
                     "status": check.status, "exit": getattr(check, "exit", None),
                     "wall_s": getattr(check, "wall_s", None)})
        data.update(capsule=capsule.model_dump(), last_commit=capsule.repo.head[:12])
        with store.locked():
            if store.read(capsule) is not None:
                return failed("duplicate", None)
        stage = "send"
        async def deliver(chosen):
            if not enabled(room, bindings):
                raise ValueError("handoff authority changed")
            await _send(daemon, relay, room, store, capsule, data, chosen, [])
            event("sent", capsule_id=capsule.id,
                  envelope_bytes=len(json.dumps(data, sort_keys=True).encode()))

        if cause == "busy":
            async def select():
                chosen, why = await select_target(
                    relay, room=room, actor=actor, task_id=task_id, failed_agents=failed_agents,
                    task=task, detect=detect, capabilities=capabilities, score=score,
                    bindings=bindings, event=event)
                return (None, why) if why else (chosen, None)

            await waits.start(daemon, relay, envelope, worktree, store, capsule, data, target,
                              attempt_id, budget, event, select, deliver)
            return "waiting"
        await deliver(target)
        return "sent"
    except Exception:
        # State/transfer/post failures can be ambiguous; the durable fence survives.
        try:
            event("attempt_failed", cause=f"{stage}_failed")
        except Exception:
            pass  # the owner alert below still goes out
        daemon._alert_owner(room, "⚠ handoff did not complete; work kept unpublished")
        return "stopped"


def decline(envelope):
    if envelope.get("message_type") != "social":
        return None
    try:
        verb = SocialVerb.model_validate_json(envelope.get("content") or "")
        payload = verb.payload.model_dump()
        if (verb.verb == "defer" and verb.actor == envelope.get("from_name")
                and payload.get("decline")
                and not payload.get("failover")):
            return verb, payload
    except ValueError:
        pass
    return None


async def retry(daemon, relay, envelope, worktree, *, detect, capabilities, score):
    """Daemon-only path: it runs even while its harness has no quota."""
    parsed = decline(envelope)
    if not parsed or worktree is None:
        return
    verb, payload = parsed
    actor, room = daemon.config.participant_name, envelope.get("room") or ""
    if payload["to"] != actor:
        return
    store = runtime.journal(daemon, room, worktree)
    item = payload["decline"]
    try:
        with store.locked():
            entries = store.pending_sends()
            entry = next((e for e in entries if e["id"] == item["capsule_id"]), None)
            if entry is None:
                return
            capsule = HandoffCapsule.model_validate(entry["snapshot"])
            if (item["fingerprint"] != fingerprint(capsule) or verb.ref_message_id != capsule.root
                    or item["task_id"] != entry["data"]["task_id"]
                    or verb.actor != entry["target"] or verb.actor in entry["declines"]):
                return
            declines = [*entry["declines"], verb.actor]
            # Record the response before candidate selection: duplicates cannot retry.
            store.write(capsule, "sent", declines=declines)
        if not await owns_claim(relay, room=room, participant=actor, task_id=item["task_id"]):
            raise ValueError("decline did not return the claim")
        target = None if len(declines) >= 3 else failover.choose_candidate(
            await relay.fetch_presence(), await relay.fetch_members(room=room), actor=actor,
            failed=[*entry["data"]["failed_agents"], *declines], task=capsule.task,
            detect=detect, capabilities=capabilities, score=score)
        if target is None:
            raise ValueError("no more receivers")
        await _send(daemon, relay, room, store, capsule, entry["data"], target, declines)
    except Exception:
        daemon._alert_owner(room, "⚠ handoff busy/retry stopped; work kept unpublished")
