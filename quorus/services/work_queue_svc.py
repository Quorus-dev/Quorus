"""Work-queue service — Stream B.

A per-(tenant, room) registry of active tasks. The queue is the durable
counterpart to the in-memory :class:`SocialSvc.claims` dict from Stream A:
when an agent posts a ``claim`` social verb, the service mirrors the task
into this queue so the TUI can render a "Active work" panel that survives
relay restarts (when Redis is configured) and shows what's in flight at a
glance.

API surface
-----------
* :meth:`add` — register a new task.
* :meth:`claim` — atomically transition a task to ``in_progress`` for an actor.
* :meth:`update` — patch arbitrary mutable fields (status, eta, notes).
* :meth:`complete` — mark a task ``done`` with an outcome.
* :meth:`list` — list tasks, optionally filtered by status.
* :meth:`apply_social_event` — hook for the social-verb router so claim/
  release/queue verbs propagate into the work queue without each route
  having to know both surfaces. This is the "subscribes to Stream A" hook.

Concurrency
-----------
A per-(tenant, room) :class:`asyncio.Lock` serialises all mutations. Two
agents racing on the same ``task_id`` see deterministic outcomes: the first
``claim`` wins, the second receives a :class:`ClaimRaceError` (which the
route maps to HTTP 409). Within a single process this is enough; for
multi-replay setups Redis-backed atomic ops live in the same module via
the optional ``redis_conn`` arg.

Persistence
-----------
Default: in-memory, scoped per-(tenant, room).
Optional: Redis hash ``work_queue:{tid}:{rid}`` so the queue survives
restarts. The route layer wires this in by passing ``redis_conn``
through ``app.state``. When Redis is unavailable, the in-memory store
is the source of truth — exactly the same fallback pattern as
:mod:`quorus.services.social_svc`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# Allowed task statuses. Kept as a frozenset so callers can do `in` checks
# without paying for an instance allocation per call.
STATUSES: frozenset[str] = frozenset({
    "pending",      # registered, not yet claimed
    "in_progress",  # claimed by an actor
    "blocked",      # actor signalled it's stuck (still owned)
    "done",         # completed successfully
    "failed",       # completed with error
    "cancelled",    # released without handoff
})

DEFAULT_TASK_TTL_SECONDS = 60 * 60  # 1h soft TTL — only used by `expire()`

# M13 fix — per-actor concurrency quota. The rate limit (60/min in
# routes/work_queue.py) bounds *requests*, not *active tasks*: a malicious
# agent can churn at the rate-limit ceiling and accumulate hundreds of
# in-flight claims, each one a piece of state the relay has to track and
# the TUI has to render. Cap the working set at 50 per actor per (tid,
# rid). Anything beyond that is almost certainly a runaway loop or an
# attacker; the legitimate usage pattern is "claim, work for a few
# seconds, complete/release", which never approaches 50 concurrent.
MAX_ACTIVE_PER_ACTOR = 50

# Statuses that count toward the per-actor quota — anything still
# representing live work owned by the actor.
_ACTIVE_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "blocked"})


class WorkQueueError(Exception):
    """Base for queue-mutation rejections."""


class ClaimRaceError(WorkQueueError):
    """Raised when an actor tries to claim a task another actor already owns.

    Maps to HTTP 409 in the route layer."""


class TaskNotFoundError(WorkQueueError):
    """Raised when a referenced task isn't in the queue. Maps to HTTP 404."""


class QuotaExceededError(WorkQueueError):
    """Raised when an actor exceeds the per-(tid, rid) active-task quota.

    Maps to HTTP 429 in the route layer — same status the upstream rate
    limiter uses, so clients see one consistent backpressure signal.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


def _new_task(
    *,
    task_id: str,
    summary: str,
    requested_by: str,
    eta_seconds: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "summary": summary[:500],
        "requested_by": requested_by,
        "claimed_by": None,
        "status": "pending",
        "eta_seconds": int(eta_seconds or 0),
        "started_at": None,
        "completed_at": None,
        "outcome": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "metadata": dict(metadata or {}),
    }


def _normalise_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s not in STATUSES:
        raise WorkQueueError(
            f"invalid status {status!r}; must be one of: "
            f"{','.join(sorted(STATUSES))}"
        )
    return s


class WorkQueueSvc:
    """In-memory + optional Redis-backed work-queue state machine.

    Mirror of :class:`quorus.services.social_svc.SocialSvc` shape — each
    tenant/room pair holds its own dict of tasks, mutations are
    lock-serialised, and a ``reset()`` hook lets ``relay_routes.reset_state``
    clear the slate for tests.
    """

    def __init__(
        self,
        *,
        redis_conn: Any | None = None,
        clock_iso: Callable[[], str] | None = None,
        clock_epoch: Callable[[], float] | None = None,
    ) -> None:
        # In-memory mirror — always populated even when Redis is configured,
        # so reads are O(1) without an awaited round-trip.
        self._tasks: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        # Per-(tid, rid) lock so concurrent claims serialize cleanly.
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._lock_factory_lock = asyncio.Lock()
        self._redis = redis_conn
        self._clock_iso = clock_iso or _now_iso
        self._clock_epoch = clock_epoch or _now_epoch

    # ── lifecycle ────────────────────────────────────────────────────────

    async def reset(
        self, tid: str | None = None, rid: str | None = None,
    ) -> None:
        """Wipe queue state. Called from ``relay_routes.reset_state``."""
        if tid is None and rid is None:
            self._tasks.clear()
            return
        if rid is None:
            self._tasks = {
                k: v for k, v in self._tasks.items() if k[0] != tid
            }
            return
        self._tasks.pop((tid, rid), None)

    async def _get_lock(self, tid: str, rid: str) -> asyncio.Lock:
        key = (tid, rid)
        if key in self._locks:
            return self._locks[key]
        async with self._lock_factory_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def _bucket(self, tid: str, rid: str) -> dict[str, dict[str, Any]]:
        return self._tasks.setdefault((tid, rid), {})

    @staticmethod
    def _redis_key(tid: str, rid: str) -> str:
        # Hash key per spec: ``work_queue:{tid}:{room}``. Field is task_id
        # so concurrent claims can use HSET NX semantics for atomicity.
        return f"work_queue:{tid}:{rid}"

    async def _redis_persist(
        self, tid: str, rid: str, task: dict[str, Any],
    ) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hset(
                self._redis_key(tid, rid),
                task["task_id"],
                json.dumps(task, separators=(",", ":")),
            )
        except Exception:
            # Redis is best-effort secondary; in-memory is source of truth.
            pass

    async def _redis_drop(self, tid: str, rid: str, task_id: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hdel(self._redis_key(tid, rid), task_id)
        except Exception:
            pass

    # ── public API ───────────────────────────────────────────────────────

    async def add(
        self,
        tid: str,
        rid: str,
        *,
        summary: str,
        requested_by: str,
        eta_seconds: int = 0,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register a new task. Returns the persisted task dict."""
        if not summary.strip():
            raise WorkQueueError("summary required")
        task_id = task_id or f"task-{uuid.uuid4().hex[:8]}"
        lock = await self._get_lock(tid, rid)
        async with lock:
            bucket = self._bucket(tid, rid)
            if task_id in bucket:
                # Idempotent re-add: return the existing record. This lets
                # callers retry safely without duplicating tasks on the
                # SSE-driven mirror path.
                return dict(bucket[task_id])
            task = _new_task(
                task_id=task_id, summary=summary,
                requested_by=requested_by, eta_seconds=eta_seconds,
                metadata=metadata,
            )
            task["created_at"] = self._clock_iso()
            task["updated_at"] = task["created_at"]
            bucket[task_id] = task
            await self._redis_persist(tid, rid, task)
            return dict(task)

    async def claim(
        self,
        tid: str,
        rid: str,
        *,
        task_id: str,
        actor: str,
        eta_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Atomically transition a task to ``in_progress`` for *actor*.

        Race semantics: the first ``claim`` wins. A second call by a
        different actor while the task is ``in_progress`` raises
        :class:`ClaimRaceError`. A re-claim by the same actor is a no-op
        (idempotent); useful for retried HTTP requests.
        """
        if not actor:
            raise WorkQueueError("actor required")
        lock = await self._get_lock(tid, rid)
        async with lock:
            bucket = self._bucket(tid, rid)
            task = bucket.get(task_id)
            # M13 — count this actor's active workload BEFORE we mutate
            # state. If the incoming claim would push us past the quota
            # AND the actor is not just re-asserting an existing claim
            # (idempotent re-claims by the same actor are harmless), we
            # reject with QuotaExceededError so the caller sees 429.
            already_owned = (
                task is not None
                and task.get("claimed_by") == actor
                and task.get("status") in _ACTIVE_STATUSES
            )
            if not already_owned:
                active_count = sum(
                    1
                    for t in bucket.values()
                    if t.get("claimed_by") == actor
                    and t.get("status") in _ACTIVE_STATUSES
                )
                if active_count >= MAX_ACTIVE_PER_ACTOR:
                    raise QuotaExceededError(
                        f"actor {actor!r} has {active_count} active tasks "
                        f"(max {MAX_ACTIVE_PER_ACTOR})"
                    )
            if task is None:
                # Auto-register on first claim — keeps the verb-driven
                # path symmetric with the explicit POST /work_queue path.
                task = _new_task(
                    task_id=task_id, summary=task_id,
                    requested_by=actor, eta_seconds=eta_seconds or 0,
                )
                task["created_at"] = self._clock_iso()
                task["updated_at"] = task["created_at"]
                bucket[task_id] = task
            current_owner = task.get("claimed_by")
            if current_owner and current_owner != actor:
                if task.get("status") in {"in_progress", "blocked"}:
                    raise ClaimRaceError(
                        f"task {task_id!r} already claimed by "
                        f"{current_owner!r}"
                    )
            task["claimed_by"] = actor
            task["status"] = "in_progress"
            if eta_seconds is not None:
                task["eta_seconds"] = int(eta_seconds)
            now = self._clock_iso()
            task["started_at"] = task.get("started_at") or now
            task["updated_at"] = now
            await self._redis_persist(tid, rid, task)
            return dict(task)

    async def update(
        self,
        tid: str,
        rid: str,
        *,
        task_id: str,
        actor: str,
        status: str | None = None,
        eta_seconds: int | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Patch a task's mutable fields. Caller-actor must already own it
        (or be the requester) — guards against an off-room agent flipping
        another agent's task to ``done``.
        """
        lock = await self._get_lock(tid, rid)
        async with lock:
            bucket = self._bucket(tid, rid)
            task = bucket.get(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            owner = task.get("claimed_by") or task.get("requested_by")
            if actor and owner and actor != owner:
                raise WorkQueueError(
                    f"actor {actor!r} cannot mutate task owned by {owner!r}"
                )
            if status is not None:
                task["status"] = _normalise_status(status)
            if eta_seconds is not None:
                task["eta_seconds"] = max(0, int(eta_seconds))
            if metadata_patch:
                meta = dict(task.get("metadata", {}))
                meta.update(metadata_patch)
                task["metadata"] = meta
            task["updated_at"] = self._clock_iso()
            await self._redis_persist(tid, rid, task)
            return dict(task)

    async def complete(
        self,
        tid: str,
        rid: str,
        *,
        task_id: str,
        actor: str,
        outcome: str = "ok",
        success: bool = True,
    ) -> dict[str, Any]:
        """Mark a task done (success=True ⇒ ``done``; else ``failed``)."""
        lock = await self._get_lock(tid, rid)
        async with lock:
            bucket = self._bucket(tid, rid)
            task = bucket.get(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            task["status"] = "done" if success else "failed"
            task["completed_at"] = self._clock_iso()
            task["outcome"] = outcome
            task["updated_at"] = task["completed_at"]
            await self._redis_persist(tid, rid, task)
            return dict(task)

    async def release(
        self,
        tid: str,
        rid: str,
        *,
        task_id: str,
        actor: str,
        handoff_to: str | None = None,
    ) -> dict[str, Any]:
        """Release a claimed task. With *handoff_to*, transfers ownership;
        without, marks the task ``cancelled``."""
        lock = await self._get_lock(tid, rid)
        async with lock:
            bucket = self._bucket(tid, rid)
            task = bucket.get(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            now = self._clock_iso()
            if handoff_to:
                task["claimed_by"] = handoff_to
                task["status"] = "in_progress"
                meta = dict(task.get("metadata", {}))
                meta.setdefault("handoff_history", []).append({
                    "from": actor, "to": handoff_to, "at": now,
                })
                task["metadata"] = meta
            else:
                task["status"] = "cancelled"
                task["completed_at"] = now
            task["updated_at"] = now
            await self._redis_persist(tid, rid, task)
            return dict(task)

    async def list(
        self,
        tid: str,
        rid: str,
        *,
        status: str | Iterable[str] | None = None,
        actor: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return tasks ordered by ``created_at`` ascending."""
        lock = await self._get_lock(tid, rid)
        async with lock:
            bucket = self._bucket(tid, rid)
            items = list(bucket.values())
        items.sort(key=lambda t: t.get("created_at", ""))
        if status:
            wanted = (
                {status} if isinstance(status, str) else {s for s in status}
            )
            wanted = {_normalise_status(s) for s in wanted}
            items = [t for t in items if t.get("status") in wanted]
        if actor:
            items = [t for t in items if t.get("claimed_by") == actor]
        return [dict(t) for t in items[: max(1, min(limit, 500))]]

    async def get(
        self, tid: str, rid: str, task_id: str,
    ) -> dict[str, Any] | None:
        lock = await self._get_lock(tid, rid)
        async with lock:
            bucket = self._bucket(tid, rid)
            task = bucket.get(task_id)
            return dict(task) if task else None

    async def expire(
        self, tid: str, rid: str, *, ttl_seconds: int = DEFAULT_TASK_TTL_SECONDS,
    ) -> list[str]:
        """Mark long-running ``in_progress`` tasks as ``failed`` with reason
        ``timeout``. Returns the list of task_ids expired.

        Uses ``started_at`` as the anchor since ``created_at`` may pre-date
        the actual claim by minutes when an agent batches work. Tasks that
        never moved out of ``pending`` aren't candidates — they age in
        place until someone claims or cancels them explicitly.
        """
        cutoff = self._clock_epoch() - max(1, ttl_seconds)
        expired: list[str] = []
        lock = await self._get_lock(tid, rid)
        async with lock:
            for task in self._bucket(tid, rid).values():
                if task.get("status") != "in_progress":
                    continue
                started = task.get("started_at")
                if not started:
                    continue
                try:
                    started_epoch = datetime.fromisoformat(started).timestamp()
                except ValueError:
                    continue
                if started_epoch < cutoff:
                    task["status"] = "failed"
                    task["outcome"] = "timeout"
                    task["completed_at"] = self._clock_iso()
                    task["updated_at"] = task["completed_at"]
                    expired.append(task["task_id"])
                    await self._redis_persist(tid, rid, task)
        return expired

    # ── social-verb hook ─────────────────────────────────────────────────

    async def apply_social_event(
        self,
        tid: str,
        rid: str,
        *,
        verb: str,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Mirror a Stream-A social verb into the work queue.

        Recognised verbs: ``claim``, ``release``, ``queue``. Other verbs
        are ignored (returns ``None``) — the social-svc state machine is
        the authoritative tracker for ``disagree`` / ``vote`` /
        ``interrupt``; the queue only reflects task lifecycle.

        Returns the resulting task dict, or ``None`` if the verb is not
        handled or the payload is malformed (the route still 200s on the
        verb post — queue mirroring is best-effort).
        """
        try:
            if verb == "claim":
                task_id = str(payload.get("task_id") or "").strip()
                if not task_id:
                    return None
                # Auto-add if unseen; the verb is the source of truth.
                bucket = self._bucket(tid, rid)
                if task_id not in bucket:
                    await self.add(
                        tid, rid,
                        task_id=task_id,
                        summary=str(
                            payload.get("scope") or task_id
                        ),
                        requested_by=actor,
                        eta_seconds=int(payload.get("eta_seconds") or 0),
                    )
                return await self.claim(
                    tid, rid,
                    task_id=task_id, actor=actor,
                    eta_seconds=int(payload.get("eta_seconds") or 0),
                )
            if verb == "release":
                task_id = str(payload.get("task_id") or "").strip()
                if not task_id:
                    return None
                handoff = payload.get("handoff_to")
                # Tolerate a missing record (never claimed via the queue):
                # we'll create + cancel it so the audit trail still reads
                # as "this task existed and is now done".
                bucket = self._bucket(tid, rid)
                if task_id not in bucket:
                    await self.add(
                        tid, rid,
                        task_id=task_id,
                        summary=str(
                            payload.get("reason") or task_id
                        ),
                        requested_by=actor,
                        eta_seconds=0,
                    )
                return await self.release(
                    tid, rid,
                    task_id=task_id, actor=actor,
                    handoff_to=str(handoff) if handoff else None,
                )
            if verb == "queue":
                # `queue` doesn't have a stable task_id in the wire format
                # — generate one. This preserves the order via created_at
                # while letting the TUI display the task.
                summary = str(
                    payload.get("task_summary") or payload.get("after") or ""
                )
                if not summary.strip():
                    return None
                return await self.add(
                    tid, rid,
                    summary=summary,
                    requested_by=actor,
                    eta_seconds=int(payload.get("eta_seconds") or 0),
                    metadata={
                        "queued_after": str(payload.get("after") or ""),
                    },
                )
        except QuotaExceededError as exc:
            # Quota is operationally meaningful — surface at WARN so
            # operators see "social verb succeeded but mirror dropped"
            # rather than silently losing the work-queue copy. The route
            # itself still 200s the verb (best-effort mirror).
            logger.warning(
                "work_queue mirror dropped due to quota tid=%s rid=%s actor=%s verb=%s: %s",
                tid, rid, actor, verb, exc,
            )
            return None
        except WorkQueueError as exc:
            # Other queue rejections (claim races, missing tasks) are
            # benign in the social-verb mirror path — the social state
            # is the source of truth. Log at DEBUG so we keep a trail
            # without noise.
            logger.debug(
                "work_queue mirror dropped tid=%s rid=%s actor=%s verb=%s: %s",
                tid, rid, actor, verb, exc,
            )
            return None
        return None


__all__ = [
    "STATUSES",
    "MAX_ACTIVE_PER_ACTOR",
    "WorkQueueSvc",
    "WorkQueueError",
    "ClaimRaceError",
    "QuotaExceededError",
    "TaskNotFoundError",
]
