"""Quorus Social Protocol v1 — in-memory state machine.

Runs alongside :mod:`quorus.routes.social`. Each ``(tenant_id, room_id)`` keeps
its own state dict (claims, blocks, defer-graph, votes, queue, social_credit).
Async-lock-protected so concurrent verb posts are serialized per process.

This is intentionally in-memory only for Stream A — Stream B owns persistence
(Redis or Postgres) and the spec doc is explicit about this. Restart wipes the
state; clients must replay from history if continuity is required.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from quorus.protocol.social_verbs import SocialVerb

_logger = logging.getLogger("quorus.services.social")

# H3 caps — soft limits per room/poll/tenant so a noisy actor cannot
# unbound state. Values chosen well above any legitimate workflow
# (rooms typically have <50 participants, polls <100 voters).
_MAX_QUEUE_PER_ROOM = 1000
_MAX_DEFER_EDGES_PER_ROOM = 256
_MAX_VOTERS_PER_POLL = 1024
_MAX_SOCIAL_CREDIT = 10_000


class SocialProtocolError(Exception):
    """Base for state-machine rejections that map to 4xx responses."""


class BlockedError(SocialProtocolError):
    """Raised when a `claim` is rejected because the room is in a
    blocking-disagree halt. Maps to HTTP 409."""


class ClaimRaceError(SocialProtocolError):
    """Raised when a `claim` is rejected because another actor already
    owns the task. Mirrors :class:`work_queue_svc.ClaimRaceError` so the
    Stream A surface refuses silent task-hijack. Maps to HTTP 409."""


class CycleError(SocialProtocolError):
    """Raised when a `defer` would close a cycle in the dependency graph.
    Maps to HTTP 400."""


class DoubleVoteError(SocialProtocolError):
    """Raised when the same actor votes twice in the same poll. Maps to
    HTTP 400."""


# Small fairness adjustments per verb. Tuned conservatively so a single
# session can't dominate the metric. Stream B may adjust.
_CREDIT_DELTA: dict[str, float] = {
    "claim": 0.05,
    "release": 0.0,
    "disagree": 0.0,
    "defer": -0.02,
    "queue": 0.0,
    "vote": 0.01,
    "interrupt": -0.10,
}


def _empty_state() -> dict[str, Any]:
    """Fresh per-room state matrix.

    Keys:
      ``claims``: ``task_id -> {actor, eta_seconds, scope, ts}``
      ``blocked_until_resolved``: bool
      ``blocking_disagree_ref``: ref_message_id of the active block (or None)
      ``defer_graph``: ``actor -> {target -> expires_at_epoch}``
      ``votes``: ``poll_id -> {option -> total_weight}``
      ``voters``: ``poll_id -> set[actor]``
      ``queue``: list of {after, task_summary, eta_seconds, ts, actor}
      ``social_credit``: ``actor -> running float`` (fairness signal)
    """
    return {
        "claims": {},
        "blocked_until_resolved": False,
        "blocking_disagree_ref": None,
        "defer_graph": {},
        "votes": {},
        "voters": {},
        "queue": [],
        "social_credit": {},
    }


class SocialSvc:
    """In-memory social-protocol state machine.

    Public surface:

    * :meth:`apply` — main entrypoint. Validates state-machine invariants and
      mutates the per-room state.
    * :meth:`get_state` — read-only snapshot for `GET /v1/social/state/{room}`.
    * :meth:`reset` — wipe state (full or scoped). Called from
      ``relay_routes.reset_state`` so tests start clean.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._state: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._clock = clock or time.time

    async def get_state(self, tid: str, rid: str) -> dict[str, Any]:
        """Return a defensive copy of the room's state dict."""
        async with self._lock:
            self._expire_defer_edges_locked(tid, rid)
            state = self._state.get((tid, rid))
            if state is None:
                return _empty_state()
            return self._snapshot(state)

    async def reset(
        self, tid: str | None = None, rid: str | None = None,
    ) -> None:
        """Wipe state. No args → full wipe. Only tid → wipe that tenant.
        Both tid+rid → wipe just that room."""
        async with self._lock:
            if tid is None and rid is None:
                self._state.clear()
                return
            if tid is not None and rid is None:
                self._state = {
                    k: v for k, v in self._state.items() if k[0] != tid
                }
                return
            self._state.pop((tid, rid), None)

    async def apply(
        self, tid: str, rid: str, sv: SocialVerb,
        *,
        room_member_count: int | None = None,
    ) -> dict[str, Any]:
        """Apply *sv* to the (tid, rid) state. Returns a small state-change
        summary the route uses as the response body's ``state_change`` field.

        ``room_member_count`` (H2 fix): when supplied, votes require both a
        quorum (ceil(N/2) distinct voters) AND a strict majority of weight.
        When ``None`` (legacy callers) we fall back to the strict-majority
        check only — the route layer should always pass it.

        Raises one of the SocialProtocolError subclasses on rejection.
        """
        async with self._lock:
            self._expire_defer_edges_locked(tid, rid)
            state = self._state.setdefault((tid, rid), _empty_state())
            verb = sv.verb
            payload = (
                sv.payload if isinstance(sv.payload, dict)
                else sv.payload.model_dump()
            )
            if verb == "vote":
                result = self._apply_vote(
                    state, sv, payload,
                    room_member_count=room_member_count,
                )
            else:
                handler = {
                    "claim": self._apply_claim,
                    "release": self._apply_release,
                    "disagree": self._apply_disagree,
                    "defer": self._apply_defer,
                    "queue": self._apply_queue,
                    "interrupt": self._apply_interrupt,
                }[verb]
                result = handler(state, sv, payload)
            # H3 cap fairness/social_credit dict per tenant.
            self._cap_room_state(state, tid)
            # Fairness counter — tiny nudge per verb. Caller-side noise.
            delta = _CREDIT_DELTA.get(verb, 0.0)
            if delta:
                credit = state["social_credit"]
                credit[sv.actor] = credit.get(sv.actor, 0.0) + delta
            return result

    # ------------------------------------------------------------------
    # Per-verb handlers — each returns a small dict for the response.
    # All handlers run with self._lock held.
    # ------------------------------------------------------------------

    def _apply_claim(
        self, state: dict, sv: SocialVerb, payload: dict,
    ) -> dict[str, Any]:
        if state.get("blocked_until_resolved"):
            raise BlockedError(
                f"room blocked by {state.get('blocking_disagree_ref')!r}"
            )
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            raise SocialProtocolError("claim.task_id required")
        # Reject silent task hijack — second actor cannot overwrite an
        # active claim. A re-claim by the same actor extends/refreshes
        # the existing claim (idempotent).
        existing = state["claims"].get(task_id)
        if existing and existing.get("actor") != sv.actor:
            raise ClaimRaceError(
                f"task_id={task_id!r} already claimed by "
                f"{existing.get('actor')!r}"
            )
        state["claims"][task_id] = {
            "actor": sv.actor,
            "eta_seconds": int(payload.get("eta_seconds") or 0),
            "scope": str(payload.get("scope") or ""),
            "ts": sv.ts,
        }
        return {"action": "claimed", "task_id": task_id}

    def _apply_release(
        self, state: dict, sv: SocialVerb, payload: dict,
    ) -> dict[str, Any]:
        task_id = str(payload.get("task_id") or "")
        prior = state["claims"].pop(task_id, None)
        handoff = payload.get("handoff_to")
        if handoff:
            state["claims"][task_id] = {
                "actor": str(handoff),
                "eta_seconds": int(prior.get("eta_seconds", 0)) if prior else 0,
                "scope": str(prior.get("scope", "")) if prior else "",
                "ts": sv.ts,
                "handoff_from": sv.actor,
            }
            # Hand-off to the disagreer clears the block.
            if (
                state.get("blocked_until_resolved")
                and state.get("blocking_disagree_ref")
            ):
                # We don't know the disagreer's name from ref_message_id alone;
                # cleared on any handoff to a participant on the disagree side.
                # The conservative rule is: a handoff resolves the impasse.
                state["blocked_until_resolved"] = False
                state["blocking_disagree_ref"] = None
        return {
            "action": "released",
            "task_id": task_id,
            "handoff_to": handoff,
        }

    def _apply_disagree(
        self, state: dict, sv: SocialVerb, payload: dict,
    ) -> dict[str, Any]:
        mode = str(payload.get("mode") or "advisory")
        ref = str(payload.get("ref_message_id") or "")
        if mode == "blocking":
            state["blocked_until_resolved"] = True
            state["blocking_disagree_ref"] = ref
            # Open an auto-poll keyed by the disagreed-with message.
            poll_id = f"vote_{ref}"
            state["votes"].setdefault(poll_id, {})
            state["voters"].setdefault(poll_id, set())
            return {
                "action": "blocked",
                "ref_message_id": ref,
                "auto_poll_id": poll_id,
            }
        return {"action": "advisory_disagree", "ref_message_id": ref}

    def _apply_defer(
        self, state: dict, sv: SocialVerb, payload: dict,
    ) -> dict[str, Any]:
        target = str(payload.get("to") or "")
        if not target:
            raise SocialProtocolError("defer.to required")
        ttl = int(payload.get("ttl_seconds") or 300)
        graph = state["defer_graph"]
        # Cycle check — would adding (actor → target) close a cycle?
        # We have to walk forward from `target`; if we ever land back on actor,
        # that's a cycle. Stale edges are already pruned by _expire above.
        if self._defer_creates_cycle(graph, sv.actor, target):
            raise CycleError(
                f"defer({sv.actor} -> {target}) would create a cycle"
            )
        # H3 cap — bound outstanding defer edges per room. We count
        # active edges across the whole graph (not just per src).
        active = sum(len(edges) for edges in graph.values())
        if active >= _MAX_DEFER_EDGES_PER_ROOM:
            raise SocialProtocolError(
                f"defer-graph cap reached ({_MAX_DEFER_EDGES_PER_ROOM})"
            )
        edges = graph.setdefault(sv.actor, {})
        edges[target] = self._clock() + ttl
        return {
            "action": "deferred",
            "to": target,
            "ttl_seconds": ttl,
        }

    def _apply_queue(
        self, state: dict, sv: SocialVerb, payload: dict,
    ) -> dict[str, Any]:
        entry = {
            "actor": sv.actor,
            "after": str(payload.get("after") or ""),
            "task_summary": str(payload.get("task_summary") or ""),
            "eta_seconds": int(payload.get("eta_seconds") or 0),
            "ts": sv.ts,
        }
        queue = state["queue"]
        # H3 cap — bound queue per room. Drop oldest with WARN log.
        if len(queue) >= _MAX_QUEUE_PER_ROOM:
            dropped = queue.pop(0)
            _logger.warning(
                "social_queue_evict actor=%s after=%s cap=%d dropped_actor=%s",
                sv.actor, entry["after"], _MAX_QUEUE_PER_ROOM,
                dropped.get("actor", "?"),
            )
        queue.append(entry)
        return {"action": "queued", "after": entry["after"]}

    def _apply_vote(
        self, state: dict, sv: SocialVerb, payload: dict,
        *,
        room_member_count: int | None = None,
    ) -> dict[str, Any]:
        option = str(payload.get("option") or "")
        if not option:
            raise SocialProtocolError("vote.option required")
        weight = float(payload.get("weight") or 1.0)

        # H10 fix — reject implicit "_default" poll_id when no active
        # blocking-disagree exists. A single shared bucket let voting
        # once on _default freeze across unrelated polls. Callers MUST
        # supply ``poll_id`` explicitly unless they're voting in the
        # currently-active block.
        explicit_poll = payload.get("poll_id")
        active_block_ref = state.get("blocking_disagree_ref")
        if explicit_poll:
            poll_id = str(explicit_poll)
        elif active_block_ref:
            poll_id = f"vote_{active_block_ref}"
        elif sv.ref_message_id:
            poll_id = f"vote_{sv.ref_message_id}"
        else:
            raise SocialProtocolError(
                "vote requires poll_id when not resolving an active "
                "blocking-disagree"
            )

        voters = state["voters"].setdefault(poll_id, set())
        if sv.actor in voters:
            raise DoubleVoteError(
                f"actor {sv.actor!r} already voted in {poll_id!r}"
            )
        # H3 cap — bound voters set per poll.
        if len(voters) >= _MAX_VOTERS_PER_POLL:
            raise SocialProtocolError(
                f"poll {poll_id!r} reached voter cap "
                f"({_MAX_VOTERS_PER_POLL})"
            )
        voters.add(sv.actor)
        tally = state["votes"].setdefault(poll_id, {})
        tally[option] = tally.get(option, 0.0) + weight

        # Resolve block if quorum + majority winner exists in the blocking poll.
        cleared = False
        active_block_poll = (
            f"vote_{state['blocking_disagree_ref']}"
            if state.get("blocking_disagree_ref") else None
        )
        if active_block_poll and poll_id == active_block_poll:
            if self._has_majority(
                tally, voters, room_member_count=room_member_count,
            ):
                state["blocked_until_resolved"] = False
                state["blocking_disagree_ref"] = None
                cleared = True
        return {
            "action": "voted",
            "poll_id": poll_id,
            "option": option,
            "weight": weight,
            "block_cleared": cleared,
        }

    def _apply_interrupt(
        self, state: dict, sv: SocialVerb, payload: dict,
    ) -> dict[str, Any]:
        return {
            "action": "interrupt",
            "ref_message_id": str(payload.get("ref_message_id") or ""),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _expire_defer_edges_locked(self, tid: str, rid: str) -> None:
        state = self._state.get((tid, rid))
        if not state:
            return
        now = self._clock()
        graph = state["defer_graph"]
        for src in list(graph.keys()):
            edges = graph[src]
            for tgt in list(edges.keys()):
                if float(edges[tgt]) < now:
                    edges.pop(tgt, None)
            if not edges:
                graph.pop(src, None)

    def _defer_creates_cycle(
        self, graph: dict[str, dict[str, float]], src: str, tgt: str,
    ) -> bool:
        """Walk forward from `tgt` through unexpired edges. If we ever
        reach `src`, the new edge (src→tgt) would close a cycle.
        """
        if src == tgt:
            return True
        seen: set[str] = set()
        stack: list[str] = [tgt]
        now = self._clock()
        while stack:
            node = stack.pop()
            if node == src:
                return True
            if node in seen:
                continue
            seen.add(node)
            for nxt, expires in graph.get(node, {}).items():
                if float(expires) >= now:
                    stack.append(nxt)
        return False

    def _has_majority(
        self,
        tally: dict[str, float],
        voters: set[str] | None = None,
        *,
        room_member_count: int | None = None,
    ) -> bool:
        """True when a single option strictly exceeds 50% of total weight
        AND (when room_member_count is known) at least ceil(N/2) distinct
        voters have participated.

        H2 fix: a single voter posting weight=1 cannot pass alone.
        """
        total = sum(tally.values())
        if total <= 0.0:
            return False
        leader = max(tally.values())
        majority = leader > total * 0.5
        if not majority:
            return False
        if room_member_count is None or room_member_count <= 1:
            # Without member-count context fall back to the legacy
            # strict-majority semantics (still defended by the new
            # voter cap and route-level quorum check).
            return True
        quorum_required = (room_member_count + 1) // 2
        if voters is None:
            return False
        return len(voters) >= quorum_required

    def _cap_room_state(self, state: dict[str, Any], tid: str) -> None:
        """Apply H3 caps that don't fit cleanly inside a single verb path.

        * social_credit dict per room: cap to ``_MAX_SOCIAL_CREDIT`` actors,
          LRU-evict via insertion order so the noisiest recent actor stays.
        * defer_graph: prune empty src buckets so the active-edge count stays
          honest after expirations.
        """
        credit = state.get("social_credit", {})
        if len(credit) > _MAX_SOCIAL_CREDIT:
            # Keep the newest _MAX_SOCIAL_CREDIT entries (insertion order).
            keep = list(credit.items())[-_MAX_SOCIAL_CREDIT:]
            state["social_credit"] = dict(keep)
        graph = state.get("defer_graph", {})
        for src in list(graph.keys()):
            if not graph[src]:
                graph.pop(src, None)

    def _snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON-safe deep-ish copy (sets → sorted lists)."""
        return {
            "claims": {k: dict(v) for k, v in state["claims"].items()},
            "blocked_until_resolved": bool(state.get("blocked_until_resolved")),
            "blocking_disagree_ref": state.get("blocking_disagree_ref"),
            "defer_graph": {
                src: dict(edges)
                for src, edges in state["defer_graph"].items()
            },
            "votes": {
                p: dict(t) for p, t in state["votes"].items()
            },
            "voters": {
                p: sorted(actors) for p, actors in state["voters"].items()
            },
            "queue": [dict(q) for q in state["queue"]],
            "social_credit": dict(state["social_credit"]),
        }


__all__ = [
    "SocialSvc",
    "SocialProtocolError",
    "BlockedError",
    "ClaimRaceError",
    "CycleError",
    "DoubleVoteError",
]
