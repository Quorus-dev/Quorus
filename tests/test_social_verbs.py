"""Tests for the Quorus Social Protocol v1 wire types.

Schema-only — no relay, no SSE, no state machine. Tests:

* round-trip serialize/parse for every verb
* validation rejects bad payloads (negative eta, oversize scope, …)
* ts auto-fill when blank, preserved when given
* ref_message_id round-trips
* to_envelope() yields stable key order
* parse_envelope accepts JSON string
* extra payload keys forbidden
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from quorus.protocol.social_verbs import (
    ClaimPayload,
    DeferPayload,
    DisagreePayload,
    InterruptPayload,
    QueuePayload,
    ReleasePayload,
    SocialVerb,
    VotePayload,
    parse_envelope,
)

# ---------------------------------------------------------------------------
# Round-trip serialize/parse — one test per verb (7 tests)
# ---------------------------------------------------------------------------


def test_claim_round_trip():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "claim", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {"task_id": "t-42", "eta_seconds": 600, "scope": "ship"},
    })
    assert isinstance(sv.payload, ClaimPayload)
    assert sv.payload.task_id == "t-42"
    assert sv.payload.eta_seconds == 600

    back = parse_envelope(sv.to_envelope())
    assert back.actor == "alice"
    assert back.payload.task_id == "t-42"  # type: ignore[union-attr]


def test_release_round_trip():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "release", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {
            "task_id": "t-42",
            "reason": "blocked on bob",
            "handoff_to": "bob",
        },
    })
    assert isinstance(sv.payload, ReleasePayload)
    assert sv.payload.handoff_to == "bob"
    back = parse_envelope(sv.to_envelope())
    assert back.payload.handoff_to == "bob"  # type: ignore[union-attr]


def test_disagree_round_trip_blocking():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "disagree", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {
            "ref_message_id": "msg-99",
            "reason": "violates policy",
            "mode": "blocking",
        },
    })
    assert isinstance(sv.payload, DisagreePayload)
    assert sv.payload.mode == "blocking"
    back = parse_envelope(sv.to_envelope())
    assert back.payload.mode == "blocking"  # type: ignore[union-attr]


def test_defer_round_trip_default_ttl():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "defer", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {"to": "bob"},
    })
    assert isinstance(sv.payload, DeferPayload)
    assert sv.payload.ttl_seconds == 300  # default
    assert sv.payload.to == "bob"


def test_queue_round_trip():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "queue", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {
            "after": "msg-42",
            "task_summary": "follow up on test fixtures",
            "eta_seconds": 1200,
        },
    })
    assert isinstance(sv.payload, QueuePayload)
    assert sv.payload.after == "msg-42"


def test_vote_round_trip_default_weight():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "vote", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {"option": "approve"},
    })
    assert isinstance(sv.payload, VotePayload)
    assert sv.payload.weight == 1.0
    assert sv.payload.option == "approve"


def test_interrupt_round_trip():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "interrupt", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {"ref_message_id": "msg-99", "reason": "prod is down"},
    })
    assert isinstance(sv.payload, InterruptPayload)
    assert sv.payload.reason == "prod is down"


# ---------------------------------------------------------------------------
# Validation rejects bad payloads (7 tests)
# ---------------------------------------------------------------------------


def test_claim_rejects_negative_eta():
    with pytest.raises(ValidationError):
        SocialVerb.model_validate({
            "kind": "social", "verb": "claim", "actor": "alice",
            "room_id": "r1",
            "payload": {"task_id": "t1", "eta_seconds": -1, "scope": "x"},
        })


def test_claim_rejects_oversize_scope():
    with pytest.raises(ValidationError):
        SocialVerb.model_validate({
            "kind": "social", "verb": "claim", "actor": "alice",
            "room_id": "r1",
            "payload": {
                "task_id": "t1", "eta_seconds": 30,
                "scope": "x" * 600,  # max=500
            },
        })


def test_release_rejects_empty_reason():
    with pytest.raises(ValidationError):
        SocialVerb.model_validate({
            "kind": "social", "verb": "release", "actor": "alice",
            "room_id": "r1",
            "payload": {"task_id": "t1", "reason": ""},
        })


def test_disagree_rejects_invalid_mode():
    with pytest.raises(ValidationError):
        SocialVerb.model_validate({
            "kind": "social", "verb": "disagree", "actor": "alice",
            "room_id": "r1",
            "payload": {
                "ref_message_id": "m1",
                "reason": "no",
                "mode": "soft",
            },
        })


def test_defer_rejects_ttl_below_floor():
    with pytest.raises(ValidationError):
        SocialVerb.model_validate({
            "kind": "social", "verb": "defer", "actor": "alice",
            "room_id": "r1",
            "payload": {"to": "bob", "ttl_seconds": 5},  # min=10
        })


def test_vote_rejects_negative_weight():
    with pytest.raises(ValidationError):
        SocialVerb.model_validate({
            "kind": "social", "verb": "vote", "actor": "alice",
            "room_id": "r1",
            "payload": {"option": "yes", "weight": -1.0},
        })


def test_unknown_verb_rejected():
    with pytest.raises(ValidationError):
        SocialVerb.model_validate({
            "kind": "social", "verb": "fly", "actor": "alice",
            "room_id": "r1",
            "payload": {"x": 1},
        })


# ---------------------------------------------------------------------------
# Envelope semantics (6 tests)
# ---------------------------------------------------------------------------


def test_ts_autofill_when_empty():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "claim", "actor": "alice",
        "room_id": "r1",
        "payload": {"task_id": "t1", "eta_seconds": 30, "scope": "x"},
    })
    # ISO-8601-ish: contains "T" and a "+00:00" or "Z"
    assert "T" in sv.ts
    assert any(suffix in sv.ts for suffix in ("+00:00", "Z", "+0000"))


def test_ts_preserved_when_provided():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "claim", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {"task_id": "t1", "eta_seconds": 30, "scope": "x"},
    })
    assert sv.ts == "2026-05-03T08:00:00Z"


def test_ref_message_id_propagates():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "interrupt", "actor": "alice",
        "room_id": "r1", "ref_message_id": "msg-12",
        "payload": {"ref_message_id": "msg-12", "reason": "go"},
    })
    env = sv.to_envelope()
    assert env["ref_message_id"] == "msg-12"
    back = parse_envelope(env)
    assert back.ref_message_id == "msg-12"


def test_to_envelope_canonical_key_order():
    sv = SocialVerb.model_validate({
        "kind": "social", "verb": "claim", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {"task_id": "t1", "eta_seconds": 30, "scope": "x"},
    })
    env = sv.to_envelope()
    expected_order = [
        "kind", "verb", "actor", "room_id", "ref_message_id",
        "ts", "payload",
    ]
    assert list(env.keys()) == expected_order


def test_parse_envelope_from_string():
    s = json.dumps({
        "kind": "social", "verb": "queue", "actor": "alice",
        "room_id": "r1", "ts": "2026-05-03T08:00:00Z",
        "payload": {
            "after": "m1", "task_summary": "next up", "eta_seconds": 60,
        },
    })
    sv = parse_envelope(s)
    assert sv.verb == "queue"
    assert sv.payload.task_summary == "next up"  # type: ignore[union-attr]


def test_payload_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        SocialVerb.model_validate({
            "kind": "social", "verb": "claim", "actor": "alice",
            "room_id": "r1",
            "payload": {
                "task_id": "t1", "eta_seconds": 30, "scope": "x",
                "secret_field": "nope",
            },
        })
