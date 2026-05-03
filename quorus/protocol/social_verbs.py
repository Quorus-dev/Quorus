"""Quorus Social Protocol v1 — wire types.

Apache-2.0 licensed. The canonical wire-format reference is
``docs/SOCIAL_PROTOCOL_v1.md``. This module is the reference implementation.

Seven verbs, one envelope:

    claim, release, disagree, defer, queue, vote, interrupt

Envelope::

    {
      "kind": "social",
      "verb": "<one of seven>",
      "actor": "<participant>",
      "room_id": "<uuid or name>",
      "ref_message_id": "<optional>",
      "ts": "<iso8601 utc, auto-filled if blank>",
      "payload": { ... per-verb shape ... }
    }

Pydantic v2 model — fields validated in declaration order so the
``payload`` pre-validator can read ``info.data["verb"]`` to dispatch on
the per-verb payload schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Union

from pydantic import BaseModel, Field, ValidationInfo, field_validator

VERBS: tuple[str, ...] = (
    "claim", "release", "disagree", "defer", "queue", "vote", "interrupt",
)
Verb = Literal[
    "claim", "release", "disagree", "defer", "queue", "vote", "interrupt"
]
Mode = Literal["blocking", "advisory"]


class ClaimPayload(BaseModel):
    """`/claim <task_id> <eta_seconds> <scope>`."""

    model_config = {"extra": "forbid"}

    task_id: str = Field(min_length=1, max_length=200)
    eta_seconds: int = Field(ge=0, le=86_400)
    scope: str = Field(min_length=1, max_length=500)


class ReleasePayload(BaseModel):
    """`/release <task_id> <reason> [handoff_to]`."""

    model_config = {"extra": "forbid"}

    task_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    handoff_to: str | None = Field(default=None, max_length=200)


class DisagreePayload(BaseModel):
    """`/disagree blocking|advisory <reason>`. Blocking-mode halts new claims
    in the room until a handoff or vote-majority resolves it."""

    model_config = {"extra": "forbid"}

    ref_message_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    mode: Mode = "advisory"


class DeferPayload(BaseModel):
    """`/defer to=X` — explicit dependency edge with TTL."""

    model_config = {"extra": "forbid"}

    to: str = Field(min_length=1, max_length=200)
    ref_message_id: str | None = Field(default=None, max_length=200)
    ttl_seconds: int = Field(default=300, ge=10, le=3600)


class QueuePayload(BaseModel):
    """`/queue after=#42 <summary> <eta_seconds>` — declare a follow-up."""

    model_config = {"extra": "forbid"}

    after: str = Field(min_length=1, max_length=200)
    task_summary: str = Field(min_length=1, max_length=500)
    eta_seconds: int = Field(ge=0, le=86_400)


class VotePayload(BaseModel):
    """`/vote <option> [weight]`. Default weight=1.0."""

    model_config = {"extra": "forbid"}

    poll_id: str | None = Field(default=None, max_length=200)
    option: str = Field(min_length=1, max_length=200)
    weight: float = Field(default=1.0, ge=0.0, le=10.0)


class InterruptPayload(BaseModel):
    """`/interrupt <ref_message_id> <reason>` — high-priority break-in."""

    model_config = {"extra": "forbid"}

    ref_message_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


Payload = Union[
    ClaimPayload, ReleasePayload, DisagreePayload, DeferPayload,
    QueuePayload, VotePayload, InterruptPayload,
]

_PAYLOAD_BY_VERB: dict[str, type[BaseModel]] = {
    "claim": ClaimPayload,
    "release": ReleasePayload,
    "disagree": DisagreePayload,
    "defer": DeferPayload,
    "queue": QueuePayload,
    "vote": VotePayload,
    "interrupt": InterruptPayload,
}


class SocialVerb(BaseModel):
    """Wire envelope. Pydantic v2 — fields validated in declaration order.

    The `payload` pre-validator reads `info.data["verb"]` to dispatch onto
    the correct typed payload class. Because `verb` is declared before
    `payload`, it is always available when the payload validator runs.
    """

    model_config = {"extra": "forbid"}

    kind: Literal["social"] = "social"
    verb: Verb
    actor: str = Field(min_length=1, max_length=200)
    room_id: str = Field(min_length=1, max_length=200)
    ref_message_id: str | None = Field(default=None, max_length=200)
    # ``ts`` auto-fills via default_factory + a validator that also fires on
    # explicit empty strings (the common case when callers set ``ts=""``).
    ts: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    payload: dict | Payload  # dict on inbound, parsed Payload after _coerce

    @field_validator("ts", mode="before")
    @classmethod
    def _autofill_ts(cls, v):
        if not v:
            return datetime.now(timezone.utc).isoformat()
        return v

    @field_validator("payload", mode="before")
    @classmethod
    def _coerce_payload(cls, v, info: ValidationInfo):
        # info.data is populated for fields validated AFTER `verb` (declaration
        # order). `verb` is declared before `payload`, so it's always present.
        verb = info.data.get("verb") if info and info.data else None
        if not verb:
            return v
        cls_ = _PAYLOAD_BY_VERB.get(verb)
        if cls_ is None:
            return v
        if isinstance(v, cls_):
            return v
        if isinstance(v, dict):
            return cls_.model_validate(v)
        raise ValueError(
            f"payload for verb={verb!r} must be a dict or {cls_.__name__}"
        )

    def to_envelope(self) -> dict:
        """Wire-format JSON-safe dict. Stable key order matches the spec doc."""
        d = self.model_dump(mode="json")
        return {
            "kind": d["kind"],
            "verb": d["verb"],
            "actor": d["actor"],
            "room_id": d["room_id"],
            "ref_message_id": d.get("ref_message_id"),
            "ts": d["ts"],
            "payload": d["payload"],
        }


def parse_envelope(data: dict | str) -> SocialVerb:
    """Parse a wire envelope into a typed :class:`SocialVerb`. Raises on
    invalid input (Pydantic ``ValidationError`` or ``json.JSONDecodeError``)."""
    if isinstance(data, str):
        import json as _json
        data = _json.loads(data)
    return SocialVerb.model_validate(data)


__all__ = [
    "VERBS",
    "Verb",
    "Mode",
    "ClaimPayload",
    "ReleasePayload",
    "DisagreePayload",
    "DeferPayload",
    "QueuePayload",
    "VotePayload",
    "InterruptPayload",
    "SocialVerb",
    "parse_envelope",
]
