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
import time
from typing import Any, Callable

from quorus.protocol.social_verbs import SocialVerb


class SocialProtocolError(Exception):
    """Base for state-machine rejections that map to 4xx responses."""


class BlockedError(SocialProtocolError):
    """Raised when a `claim` is rejected because the room is in a
    blocking-disagree halt. Maps to HTTP 409."""


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
    ) -> dict[str, Any]:
        """Apply *sv* to the (tid, rid) state. Returns a small state-change
        summary the route uses as the response body's ``state_change`` field.

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
            handler = {
                "claim": self._apply_claim,
                "release": self._apply_release,
                "disagree": self._apply_disagree,
                "defer": self._apply_defer,
                "queue": self._apply_queue,
                "vote": self._apply_vote,
                "interrupt": self._apply_interrupt,
            }[verb]
            result = handler(state, sv, payload)
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
        state["queue"].append(entry)
        return {"action": "queued", "after": entry["after"]}

    def _apply_vote(
        self, state: dict, sv: SocialVerb, payload: dict,
    ) -> dict[str, Any]:
        option = str(payload.get("option") or "")
        if not option:
            raise SocialProtocolError("vote.option required")
        weight = float(payload.get("weight") or 1.0)
        # Resolve poll_id: prefer explicit, else the active block, else default.
        poll_id = (
            payload.get("poll_id")
            or (
                f"vote_{state.get('blocking_disagree_ref')}"
                if state.get("blocking_disagree_ref")
                else None
            )
            or (
                f"vote_{sv.ref_message_id}" if sv.ref_message_id else "_default"
            )
        )
        voters = state["voters"].setdefault(poll_id, set())
        if sv.actor in voters:
            raise DoubleVoteError(
                f"actor {sv.actor!r} already voted in {poll_id!r}"
            )
        voters.add(sv.actor)
        tally = state["votes"].setdefault(poll_id, {})
        tally[option] = tally.get(option, 0.0) + weight

        # Resolve block if a majority winner exists in the blocking poll.
        cleared = False
        active_block_poll = (
            f"vote_{state['blocking_disagree_ref']}"
            if state.get("blocking_disagree_ref") else None
        )
        if active_block_poll and poll_id == active_block_poll:
            if self._has_majority(tally):
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

    def _has_majority(self, tally: dict[str, float]) -> bool:
        """True when a single option strictly exceeds 50% of total weight."""
        total = sum(tally.values())
        if total <= 0.0:
            return False
        leader = max(tally.values())
        return leader > total * 0.5

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
    "CycleError",
    "DoubleVoteError",
]
