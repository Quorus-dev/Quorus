"""Capsules carry bounded evidence, never executable authority."""

import copy
import hashlib

import pytest

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime.check_config import CheckConfig
from quorus.runtime.handoff_capsule import build_capsule


def arguments():
    config = CheckConfig(("python", "-m", "pytest"))
    return dict(root="request", hop=1, actor="starter-claude", task="fix the test",
                repo={"main": "main", "base": "a" * 40, "branch": "quorus/starter-claude",
                      "head": "b" * 40, "tree": "c" * 40,
                      "unfinished": "quorus/starter-claude-unfinished"},
                checkpoint={"status": "ran", "head": "b" * 40, "tree": "c" * 40,
                            **config.record(), "config_id": config.config_id, "exit": 1,
                            "ran_at": "2026-10-21T14:00:00Z", "wall_s": 1.0,
                            "tail": "FAILED test_x", "tail_sha256": hashlib.sha256(
                                b"FAILED test_x").hexdigest()},
                config=config, tried=["first approach failed"],
                limit={"vendor": "claude", "reason": "quota", "kind": "observed",
                       "source": "msg.retry_in", "seen_at": "2026-10-21T14:00:00Z",
                       "resets_at": "2026-10-21T15:00:00Z"},
                spent={"turns": 1, "wall_s": 50.0, "attempts": 1})


def test_roundtrip_keeps_snapshot_failure_and_config_bound():
    value = build_capsule(**arguments())
    assert HandoffCapsule.model_validate_json(value.model_dump_json()) == value
    assert value.repo.head == value.check.head and value.repo.tree == value.check.tree
    assert value.check.exit == 1 and value.check.status == "ran"


@pytest.mark.parametrize("field,value", [("head", "d" * 40), ("tree", "d" * 40),
                                         ("config_id", "d" * 64), ("exit", 0),
                                         ("exit", True), ("tail_sha256", "d" * 64),
                                         ("argv", ["arbitrary-command"])])
def test_forged_or_mismatched_checkpoint_rejected(field, value):
    args = arguments()
    args["checkpoint"][field] = value
    with pytest.raises(ValueError):
        build_capsule(**args)


def test_no_config_is_explicitly_unverifiable_data():
    args = arguments()
    args.update(config=None, checkpoint={"status": "none", "head": "b" * 40})
    assert build_capsule(**args).check.status == "none"


def test_passing_check_cannot_be_relabelled_failing():
    args = arguments()
    args["checkpoint"].update(status="passing", exit=0)
    assert build_capsule(**args).check.status == "passing"
    args["checkpoint"]["status"] = "ran"
    with pytest.raises(ValueError, match="nonzero"):
        build_capsule(**args)


def test_wire_size_budget_trims_info_and_preserves_tail_end():
    args = arguments()
    args["repo"]["dirty_files"] = ["x" * 180 + str(n) for n in range(32)]
    args["tried"] = ["試" * 120] * 3
    value = build_capsule(**args)
    assert len(value.model_dump_json().encode()) <= 4096
    assert value.repo.head == "b" * 40 and value.check.head == "b" * 40
    assert len(value.repo.dirty_files) < 32


def test_redacts_descriptive_text_without_mutating_inputs():
    args = arguments()
    secret = "quorus_pk_" + "x" * 24
    args["task"] = secret
    args["tried"] = [secret]
    args["checkpoint"].update(tail=secret, tail_sha256=hashlib.sha256(secret.encode()).hexdigest())
    before = copy.deepcopy(args)
    value = build_capsule(**args)
    assert secret not in value.model_dump_json()
    assert args == before


def test_credential_in_command_is_refused_without_rewriting_argv():
    args = arguments()
    secret = "quorus_pk_" + "x" * 24
    config = CheckConfig(("pytest", "--token", secret))
    args["config"] = config
    args["checkpoint"].update(config.record(), config_id=config.config_id)
    with pytest.raises(ValueError, match="credential"):
        build_capsule(**args)


@pytest.mark.parametrize("update", [
    {"source": "raw stderr text"}, {"resets_at": float("inf")},
    {"kind": "guess"}, {"resets_at": 900.0}, {"seen_at": float("nan")},
])
def test_limit_context_cannot_invent_provenance(update):
    args = arguments()
    args["limit"].update(update)
    with pytest.raises(ValueError):
        build_capsule(**args)


def test_parser_rejects_unknown_fields_or_oversized_capsule():
    raw = build_capsule(**arguments()).model_dump()
    raw["shell"] = "untrusted"
    with pytest.raises(ValueError):
        HandoffCapsule.model_validate(raw)
    raw.pop("shell")
    raw["repo"]["dirty_files"] = ["x" * 200] * 32
    with pytest.raises(ValueError, match="4096"):
        HandoffCapsule.model_validate(raw)


def test_capsule_iso_and_presence_epochs_represent_same_instants():
    from datetime import datetime

    args = arguments()
    args["limit"].update(seen_at="2026-10-21T10:00:00.125-04:00",
                         resets_at="2026-10-21T11:00:00.125-04:00")
    value = build_capsule(**args).limit
    reading = value.presence_reading()
    assert value.window == reading["window"] == "limit"
    assert value.seen_at == "2026-10-21T14:00:00.125000+00:00"
    assert reading["seen_at"] == datetime.fromisoformat(value.seen_at).timestamp()
    assert reading["resets_at"] - reading["seen_at"] == 3600
    assert reading["source"] == "msg.retry_in"
    assert type(value).from_presence(reading, vendor="claude", reason="quota") == value
    reading["seen_at"] = True
    with pytest.raises(ValueError):
        type(value).from_presence(reading, vendor="claude", reason="quota")


@pytest.mark.parametrize("timestamp", ["2026-10-21T14:00:00", "bad", "2200-01-01T00:00:00Z"])
def test_naive_malformed_or_out_of_bounds_limit_time_rejected(timestamp):
    args = arguments()
    args["limit"]["seen_at"] = timestamp
    with pytest.raises(ValueError):
        build_capsule(**args)
