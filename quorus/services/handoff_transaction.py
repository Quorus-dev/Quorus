"""Accept a typed handoff before moving its claim; never release to nobody."""
from __future__ import annotations

import copy
import hashlib
import json

from fastapi import HTTPException

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime.failover import continuation
from quorus.runtime.handoff_state import fingerprint
from quorus.services.social_svc import _empty_state
from quorus.services.work_queue_svc import MAX_ACTIVE_PER_ACTOR


async def accept(queue, social, tid, rid, sv, members, deliver):
    payload = sv.payload.model_dump()
    context = payload.get("decline") or payload.get("failover") or {}
    target, task_id = payload["to"], context.get("task_id")
    if not task_id or target not in members or target == sv.actor:
        raise HTTPException(400, "handoff needs an owned task and another room member")
    if not payload.get("decline"):
        parsed = continuation(dict(message_type="social", from_name=sv.actor,
                                   content=sv.model_dump_json()))
        if not parsed or not context.get("capsule"):
            raise HTTPException(400, "invalid capsule handoff context")
    receipt_id = hashlib.sha256(json.dumps(
        dict(actor=sv.actor, target=target, root=sv.ref_message_id, context=context),
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    # Same lock ordering as social claims: social first, queue second. Queue
    # GET/claim/complete/expire all wait for this lock. A receiver woken during
    # durable delivery cannot check ownership until the transfer finishes.
    async with social._get_lock(tid, rid):
        async with await queue._get_lock(tid, rid):
            await queue._hydrate_once(tid, rid)
            bucket = queue._bucket(tid, rid)
            task = bucket.get(task_id)
            if task is None:
                raise HTTPException(409, "handoff task is not claimed")
            receipts = task.get("metadata", {}).get("handoff_receipts", {})
            if receipt_id in receipts:
                return {**receipts[receipt_id], "duplicate": True}
            if len(receipts) >= 128:
                raise HTTPException(409, "handoff receipt limit reached; owner needed")
            if task.get("claimed_by") != sv.actor or task.get("status") not in {
                    "in_progress", "blocked"}:
                raise HTTPException(409, "handoff claim changed")
            active = sum(t.get("claimed_by") == target and t.get("status") in {
                "in_progress", "blocked", "pending"} for t in bucket.values())
            if active >= MAX_ACTIVE_PER_ACTOR:
                raise HTTPException(429, "handoff receiver task quota reached")
            state = copy.deepcopy(social._state.get((tid, rid), _empty_state()))
            prior = state["claims"].get(task_id)
            if prior and prior["actor"] != sv.actor:
                raise HTTPException(409, "social claim changed")
            # Prepare a direct transfer. No public release or transient unowned
            # state exists. Atomic handoffs are ownership moves, not defer-graph
            # dependency edges (a busy return would otherwise create a cycle).
            social._apply_release(state, sv, dict(task_id=task_id, handoff_to=target))
            changed = copy.deepcopy(task)
            changed.update(claimed_by=target, status="in_progress", updated_at=queue._clock_iso())
            try:
                sent = await deliver()  # schema/auth/policy checked; persist defer FIRST
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(503, "handoff delivery not accepted; claim retained") from exc
            if sent.get("warning") and (not sent.get("skipped_recipients")
                                        or target in sent["skipped_recipients"]):
                raise HTTPException(503, "handoff target delivery failed; claim retained")
            capsule_fp = (context["fingerprint"] if payload.get("decline") else
                          fingerprint(HandoffCapsule.model_validate(context["capsule"])))
            receipt = dict(message_id=sent["id"], task_id=task_id, target=target,
                           fingerprint=capsule_fp)
            meta = changed.setdefault("metadata", {})
            meta.setdefault("handoff_receipts", {})[receipt_id] = receipt
            meta.setdefault("handoff_history", []).append(dict(
                **{"from": sv.actor}, to=target, at=changed["updated_at"]))
            if queue._redis is not None:
                # Unlike the legacy queue mirror, a failed persistence ACK is
                # not success. Delivery is already accepted, so even an ambiguous
                # Redis response can never move a claim without an accepted defer.
                try:
                    key = queue._redis_key(tid, rid)
                    await queue._redis.hset(key, task_id, json.dumps(changed))
                    await queue._redis.expire(key, 14 * 24 * 3600)
                except Exception as exc:
                    raise HTTPException(503, "handoff persistence uncertain; owner needed") from exc
            bucket[task_id] = changed
            social._state[(tid, rid)] = state
            return receipt
