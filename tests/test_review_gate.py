"""Review gate slice 1: the pinned reviewer and what counts as its GO
(docs/plans/review-gate.md, Codex GO on a1f25392)."""

from __future__ import annotations

import json

import pytest

from quorus.runtime import review_gate as rg

BASE = "1" * 40
TIP = "a" * 40
MID = "b" * 40
PENDING = [MID, TIP]
PIN = {"name": "rev-codex", "tenant": "t1", "claim_id": "c-rev"}


def _m(content: str, *, sender: str = "rev-codex", auth: str = "key", claim: str = "c-rev",
       tenant: str = "t1", ts: str = "2026-10-25T06:00:00", mid: str = "m1") -> dict:
    msg = {"id": mid, "from_name": sender, "content": content, "timestamp": ts,
           "sender_auth": auth, "sender_tenant": tenant}
    if auth == "key":
        msg["sender_claim_id"] = claim
    return msg


# ── the pin store ────────────────────────────────────────────────────────


def test_no_file_means_no_reviewer(tmp_path):
    assert rg.reviewer_for("r", tmp_path / "missing.json") is None


def test_save_and_read_a_key_pin(tmp_path):
    path = tmp_path / "rev.json"
    rg.save_reviewer("r", "rev-codex", tenant="t1", claim_id="c-rev", path=path)
    assert rg.reviewer_for("r", path) == PIN
    assert rg.reviewer_for("other", path) is None
    assert (path.stat().st_mode & 0o077) == 0


def test_hosted_participant_pin(tmp_path):
    path = tmp_path / "rev.json"
    rg.save_reviewer("r", "rev", tenant="t1", participant_id="p-9", path=path)
    assert rg.reviewer_for("r", path) == {"name": "rev", "tenant": "t1", "participant_id": "p-9"}


def test_unpinned_reviewer_gates_but_matches_nothing(tmp_path):
    path = tmp_path / "rev.json"
    rg.save_reviewer("r", "rev-codex", path=path)
    pin = rg.reviewer_for("r", path)
    assert pin is not None and pin["name"] == "rev-codex" and not rg.pinned(pin)
    assert rg.verdict(_m(f"✅ GO on {TIP[:8]}"), pin, PENDING) is None


@pytest.mark.parametrize("text", ["{not json", "[]", json.dumps({"r": {"name": 3}}),
                                  json.dumps({"r": None}), json.dumps({"r": {}}),
                                  json.dumps({"r": "rev-codex"})])
def test_unreadable_file_gates_every_room(tmp_path, text):
    path = tmp_path / "rev.json"
    path.write_text(text)
    pin = rg.reviewer_for("r", path)
    assert pin is not None and not rg.pinned(pin)


def test_corrupt_file_is_never_overwritten(tmp_path):
    path = tmp_path / "rev.json"
    path.write_text("{not json")
    with pytest.raises(OSError):
        rg.save_reviewer("r", "rev-codex", tenant="t1", claim_id="c", path=path)
    assert path.read_text() == "{not json"


def test_clear_reviewer(tmp_path):
    path = tmp_path / "rev.json"
    rg.save_reviewer("r", "rev-codex", tenant="t1", claim_id="c-rev", path=path)
    rg.clear_reviewer("r", path)
    assert rg.reviewer_for("r", path) is None


# ── who can approve ──────────────────────────────────────────────────────


def test_pinned_key_go_on_tip_counts():
    assert rg.verdict(_m(f"@b ✅ GO on `{TIP[:8]}`; /release the block."), PIN, PENDING) == "go"


def test_jwt_pin_go_counts():
    pin = {"name": "rev", "tenant": "t1", "participant_id": "p-9"}
    msg = _m(f"GO on {TIP[:7]}", sender="rev", auth="jwt")
    msg["sender_participant_id"] = "p-9"
    assert rg.verdict(msg, pin, PENDING) == "go"


def test_shared_secret_bearer_under_reviewer_name_cannot_approve():
    # Same relay, same name, same text: sent with the relay secret.
    assert rg.verdict(_m(f"✅ GO on {TIP[:8]}", auth="secret"), PIN, PENDING) is None


def test_reclaimed_name_with_another_key_cannot_approve():
    assert rg.verdict(_m(f"✅ GO on {TIP[:8]}", claim="c-new"), PIN, PENDING) is None


def test_other_member_and_other_tenant_cannot_approve():
    assert rg.verdict(_m(f"✅ GO on {TIP[:8]}", sender="someone"), PIN, PENDING) is None
    assert rg.verdict(_m(f"✅ GO on {TIP[:8]}", tenant="t2"), PIN, PENDING) is None


def test_pinned_key_under_another_name_cannot_approve():
    assert rg.verdict(_m(f"✅ GO on {TIP[:8]}", sender="rev-claude"), PIN, PENDING) is None


# ── what a GO says ───────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    f"✅ GO on {MID[:8]}",                       # an older commit, not the tip
    f"✅ looks fine {TIP[:8]}",                  # no GO word
    f"no GO yet on {TIP[:8]}",                  # negated
    f"can't GO on {TIP[:8]} until tests pass",  # negated
    f"GO on {TIP[:8]}?",                        # a question
    f"if CI passes, GO on {TIP[:8]}",           # conditional
    f"> ✅ GO on {TIP[:8]}",                     # quoting someone
    f"```\n✅ GO on {TIP[:8]}\n```",             # pasted output
    f"(quorus) ✅ GO on {TIP[:8]}",              # a daemon status line
    f"✅ GO on {TIP[:6]}",                       # prefix too short
    f"✅ GO on {TIP[:8]} /disagree advisory",    # disagree in the same clause
    "✅ GO",                                     # no sha at all
    f"GO on {TIP[:8]} after CI passes",         # waits on CI (codex, 30601459)
    f"GO on {TIP[:8]} when tests pass",
    f"GO on {TIP[:8]} once the suite is green",
    f"✅ GO on {TIP[:8]} pending the full suite",
    f"GO on {TIP[:8]}? Waiting for tests",
    f"✅ GO on {TIP[:8]}. After CI passes, publish",
    f"✅ GO on {TIP[:8]}; waiting on the suite",
    f"✅ GO on {TIP[:8]}. Can you confirm the suite?",
    f"After CI passes; GO on {TIP[:8]}",          # condition before (codex, 79a256bb)
    f"Once the suite is green, ✅ GO on {TIP[:8]}",
    f"GO on {TIP[:8]}; correction: no GO yet",    # retraction after (codex, 79a256bb)
    f"✅ GO on {TIP[:8]}. Scratch that, found a bug",
    f"✅ GO on {TIP[:8]}; GO withdrawn",
    f"✅ GO on {TIP[:8]}; not a GO until docs land",
])
def test_not_a_go(text):
    assert rg.verdict(_m(text), PIN, PENDING) is None


def test_go_clause_ignores_later_negation():
    assert rg.verdict(_m(f"✅ GO on {TIP[:8]}; no regressions."), PIN, PENDING) == "go"


def test_go_with_a_description_after_it_counts():
    text = (f"@b ✅ security GO on `{TIP[:8]}`; /release the block. Mismatches now stop "
            "before HTTP calls, and joins use the child's key.\n166 focused tests pass.")
    assert rg.verdict(_m(text), PIN, PENDING) == "go"


def test_approve_verb_counts():
    assert rg.verdict(_m(f"/approve {TIP}"), PIN, PENDING) == "go"


def test_go_for_another_agents_sha_is_not_ours():
    other = "c" * 40
    assert rg.verdict(_m(f"@x ✅ GO on {other[:8]}"), PIN, PENDING) is None


# ── blocks ───────────────────────────────────────────────────────────────


def test_block_on_any_pending_commit():
    assert rg.verdict(_m(f"/disagree blocking on `{MID[:8]}`"), PIN, PENDING) == "block"


def test_block_wins_over_go_in_the_same_message():
    text = f"✅ GO on {TIP[:8]}\n/disagree blocking on {MID[:8]}: missing test"
    assert rg.verdict(_m(text), PIN, PENDING) == "block"


def test_block_on_someone_elses_commit_holds_nothing():
    text = f"@x /disagree blocking on {'c' * 8}\n@b ✅ GO on {TIP[:8]}"
    assert rg.verdict(_m(text), PIN, PENDING) == "go"


def test_block_from_non_reviewer_holds_nothing():
    assert rg.verdict(_m(f"/disagree blocking on {TIP[:8]}", sender="x"), PIN, PENDING) is None


def test_decide_folds_in_time_order():
    go = _m(f"✅ GO on {TIP[:8]}", ts="2026-10-25T06:01:00", mid="go")
    block = _m(f"/disagree blocking on {TIP[:8]}", ts="2026-10-25T06:00:00", mid="blk")
    assert rg.decide([go, block], PIN, PENDING) == ("go", "go")
    late_block = dict(block, timestamp="2026-10-25T06:02:00")
    assert rg.decide([go, late_block], PIN, PENDING) == ("blocked", "blk")
    assert rg.decide([], PIN, PENDING) == ("waiting", None)
    assert rg.decide([go], PIN, []) == ("waiting", None)
