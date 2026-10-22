"""Owner discards exactly one handoff claim under the transfer locks."""
from __future__ import annotations

import copy
import json

from fastapi import HTTPException

from quorus.services.social_svc import _empty_state


async def discard(queue, social, tid, rid, *, receiver, task_id, resolution_id):
    async with social._get_lock(tid, rid):
        async with await queue._get_lock(tid, rid):
            await queue._hydrate_once(tid, rid)
            bucket = queue._bucket(tid, rid)
            task = bucket.get(task_id)
            if task is None:
                raise HTTPException(409, "discard task not found; local hold retained")
            decisions = task.get("metadata", {}).get("handoff_discards", {})
            if resolution_id in decisions:
                if decisions[resolution_id] != receiver:
                    raise HTTPException(409, "discard decision differs")
                # Even if someone has since claimed it, don't release it twice.
                return dict(resolved=True, task_id=task_id, duplicate=True)
            receipts = [r for r in task.get("metadata", {}).get("handoff_receipts", {}).values()
                        if r.get("target") == receiver]
            if not any(r.get("fingerprint") == resolution_id for r in receipts):
                raise HTTPException(409, "no matching capsule receipt; local hold retained")
            if len(decisions) >= 128:
                raise HTTPException(409, "discard ledger full; owner resolution required")
            state = copy.deepcopy(social._state.get((tid, rid), _empty_state()))
            prior = state["claims"].get(task_id)
            if prior and prior["actor"] != receiver:
                raise HTTPException(409, "social claim changed; local hold retained")
            if task.get("claimed_by") != receiver or task.get("status") not in {
                    "in_progress", "blocked", "pending"}:
                raise HTTPException(409, "discard claim changed; local hold retained")
            changed = copy.deepcopy(task)
            changed.update(claimed_by=None, status="pending", completed_at=None,
                           updated_at=queue._clock_iso())
            changed.setdefault("metadata", {}).setdefault("handoff_discards", {})[
                resolution_id] = receiver
            state["claims"].pop(task_id, None)
            if queue._redis is not None:
                try:
                    key = queue._redis_key(tid, rid)
                    await queue._redis.hset(key, task_id, json.dumps(changed))
                    await queue._redis.expire(key, 14 * 24 * 3600)
                except Exception as exc:
                    raise HTTPException(503, "discard persistence uncertain; "
                                        "retry exact decision") from exc
            bucket[task_id] = changed
            social._state[(tid, rid)] = state
            return dict(resolved=True, task_id=task_id, duplicate=False)
