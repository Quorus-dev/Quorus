"""L2 track-record bids: an agent's own outcomes scale its @open bid."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from quorus.runtime import track_record as tr

NOW = 1_800_000_000.0


def _not_holdout() -> str:
    return next(f"m{i}" for i in range(200) if not tr.is_holdout(f"m{i}"))


def _holdout() -> str:
    return next(f"m{i}" for i in range(200) if tr.is_holdout(f"m{i}"))


@pytest.fixture(autouse=True)
def _mode(monkeypatch, tmp_path):
    monkeypatch.delenv("QUORUS_TRACK_BIDS", raising=False)
    monkeypatch.setenv("QUORUS_TUNING_FILE", str(tmp_path / "tuning.json"))


def test_no_evidence_is_neutral_and_bounds_hold():
    assert tr.Track().multiplier == 1.0
    assert tr.Track(clean=500).multiplier == tr.MAX_MULT
    assert tr.Track(errored=500).multiplier == tr.MIN_MULT
    assert tr.Track(clean=1, timeouts=1).multiplier == 1.0
    assert tr.Track(clean=4, vendor=1).multiplier > 1.0 > tr.Track(clean=1, vendor=4).multiplier


def test_availability_and_quality_are_separate_factors():
    quota = tr.Track(vendor=3, kept=8)
    assert quota.availability < 1.0 < quota.quality
    reverts = tr.Track(clean=5, reverted=3)
    assert reverts.quality < 1.0 < reverts.availability
    assert "vendor 3" in quota.reason() and "quality 8 kept 0 reverted" in quota.reason()


def test_kept_commits_are_capped_so_volume_alone_cannot_max_a_bid():
    assert tr.Track(kept=500).quality == tr.Track(kept=tr.LEDGER_KEPT_CAP).quality
    assert tr.Track(kept=500, errored=5).multiplier < tr.Track(clean=500).multiplier


def test_mode_defaults_to_shadow(monkeypatch):
    assert tr.mode() == "shadow"
    for raw, want in (("on", "on"), ("1", "on"), ("0", "off"), ("off", "off"),
                      ("nonsense", "shadow")):
        monkeypatch.setenv("QUORUS_TRACK_BIDS", raw)
        assert tr.mode() == want


def test_holdout_is_deterministic_and_about_ten_percent():
    ids = [f"msg-{i}" for i in range(4000)]
    share = sum(tr.is_holdout(i) for i in ids) / len(ids)
    assert 0.07 < share < 0.13
    assert [tr.is_holdout(i) for i in ids[:50]] == [tr.is_holdout(i) for i in ids[:50]]


@pytest.mark.parametrize("reply,vendor,reason,want", [
    ("done", False, "", "clean"),
    ("[reflexd] harness timed out", False, "", "timed_out"),
    ("[reflexd] harness errored", False, "timed out", "timed_out"),
    ("[reflexd] harness errored", False, "crashed (OSError)", "errored"),
    ("[reflexd] harness errored", False, "", "errored"),
    ("quota", True, "", "vendor_failure"),
])
def test_classify_turn(reply, vendor, reason, want):
    assert tr.classify_turn(reply, vendor_failure=vendor, failure_reason=reason) == want


def test_turns_keep_24h_only(tmp_path):
    tr.record_turn(tmp_path, "a-gemini", "errored", now=NOW - tr.WINDOW_S - 5)
    tr.record_turn(tmp_path, "a-gemini", "timed_out", now=NOW - 60)
    tr.record_turn(tmp_path, "a-gemini", "clean", now=NOW)
    tr.record_turn(tmp_path, "a-gemini", "bogus", now=NOW)
    assert [t["outcome"] for t in tr.load_turns(tmp_path, "a-gemini", NOW)] == [
        "timed_out", "clean"]


SCOPE = "relay.test|a|/repo"


def _two_failures(tmp_path):
    tr.record_turn(tmp_path, "a-gemini", "vendor_failure")
    tr.record_turn(tmp_path, "a-gemini", "timed_out")
    return dict(runtime_dir=tmp_path, participant="a-gemini", repo=None, harness="gemini",
                scope=SCOPE)


def test_counts_name_availability_never_quality():
    text = tr.counts(tr.Track(vendor=2, timeouts=1, kept=2, reverted=1))
    assert text.startswith("x") and "2 vendor fails, 1 timeout, 0 clean/24h" in text
    assert "1 of 3 commits reverted" in text and "quality" not in text


def test_shadow_keeps_the_bid_raw_but_logs_and_announces_the_would_be_bid(tmp_path):
    kw = _two_failures(tmp_path)
    mid = _not_holdout()
    d = tr.decide_bid(0.5, "open_capability_match", message_id=mid, **kw)
    assert d.bid == 0.5
    assert d.reason.startswith("open_capability_match+track(shadow) x0.")
    assert "avail 0✓ 2✗/24h (timeout 1, vendor 1)" in d.reason
    assert d.announce.startswith("(quorus) t1 (shadow, not applied): would set @open bids x0.")
    assert "1 vendor fail, 1 timeout, 0 clean/24h" in d.announce
    assert d.announce.endswith("undo: quorus tune undo t1")
    [logged] = tr.load_bids(tmp_path, "a-gemini")
    assert logged["arm"] == "shadow" and logged["raw"] == 0.5 and logged["applies"]
    assert logged["message_id"] == mid and logged["harness"] == "gemini"
    assert logged["scope"] == SCOPE


def test_on_applies_except_on_holdout_and_announces_once(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    kw = _two_failures(tmp_path)
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert d.bid < 0.5 and d.reason.startswith("r+track x0.")
    assert d.announce.startswith("(quorus) adapted t1: @open bids x0.")
    d = tr.decide_bid(0.5, "r", message_id=_holdout(), **kw)
    assert d.bid == 0.5 and d.reason.startswith("r+track(holdout) x0.") and d.announce is None
    assert [b["arm"] for b in tr.load_bids(tmp_path, "a-gemini")] == ["routed", "holdout"]


def test_hysteresis_and_cooldown_stop_flapping(tmp_path, monkeypatch):
    from quorus import tuning

    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    kw = _two_failures(tmp_path)
    for _ in range(2):
        tr.record_turn(tmp_path, "a-gemini", "errored")  # 0✓ 4✗: x0.67
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw).announce
    for _ in range(2):
        tr.record_turn(tmp_path, "a-gemini", "clean")  # 2✓ 4✗: x0.88, not past x0.9
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert 0.8 < tr.track_for(tmp_path, "a-gemini", None).multiplier < 0.9
    assert d.bid < 0.5 and d.announce is None
    for _ in range(2):
        tr.record_turn(tmp_path, "a-gemini", "clean")  # 4✓ 4✗: x1.0, it ends
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert d.bid == 0.5 and tuning.adaptations()[0].state == "retired"
    for _ in range(8):
        tr.record_turn(tmp_path, "a-gemini", "errored")  # past x0.8 again, inside 1h
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert d.bid == 0.5 and d.announce is None and len(tuning.adaptations()) == 1


def test_undo_holds_until_redo_and_tune_off_is_immediate(tmp_path, monkeypatch):
    from quorus import tuning

    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    kw = _two_failures(tmp_path)
    tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert tuning.undo("t1") is not None
    for _ in range(5):
        tr.record_turn(tmp_path, "a-gemini", "errored")
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert d.bid == 0.5 and d.announce is None and len(tuning.adaptations()) == 1
    assert tuning.redo("t1") is not None
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw).bid < 0.5
    tuning.set_enabled(False)
    assert tr.mode() == "off"
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw) == tr.BidDecision(0.5, "r")


def test_scope_keeps_adaptations_apart(tmp_path, monkeypatch):
    from quorus import tuning

    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    kw = _two_failures(tmp_path)
    tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    tuning.undo("t1")
    other = {**kw, "scope": "other-relay|a|/repo"}
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **other).announce
    assert [a.scope for a in tuning.adaptations()] == [SCOPE, "other-relay|a|/repo"]


def test_off_computes_and_logs_nothing(tmp_path, monkeypatch):
    kw = _two_failures(tmp_path)
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "off")
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw) == tr.BidDecision(0.5, "r")
    tr.record_turn(tmp_path, "a-gemini", "errored")
    assert tr.load_bids(tmp_path, "a-gemini") == []
    assert len(tr.load_turns(tmp_path, "a-gemini")) == 2


def test_no_evidence_leaves_bid_and_reason_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    for mid in (_holdout(), _not_holdout()):
        assert tr.decide_bid(0.3, "open_any_agent", message_id=mid, runtime_dir=tmp_path,
                             participant="nobody", repo=None) == tr.BidDecision(
                                 0.3, "open_any_agent")
    assert tr.load_bids(tmp_path, "nobody") == []


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_ledger_counts_published_and_reverted_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    shas = []
    for i, agent in enumerate(["a-codex", "a-codex", "a-claude"]):
        (repo / f"f{i}").write_text(str(i))
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", f"c{i}")
        sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "notes", "--ref=quorus", "add", "-m", json.dumps({"agent": agent}), sha)
        shas.append(sha)
    _git(repo, "revert", "--no-edit", shas[0])
    tr._ledger_cache.clear()
    assert tr.ledger_counts(repo, "a-codex") == (2, 1)
    assert tr.ledger_counts(repo, "a-claude") == (1, 0)
    assert tr.ledger_counts(repo, "a-gemini") == (0, 0)
    track = tr.track_for(tmp_path / "rt", "a-codex", repo)
    assert (track.kept, track.reverted) == (1, 1)
    assert "quality 1 kept 1 reverted" in track.reason()


def test_report_joins_open_wins_to_their_bids(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    tr.record_turn(tmp_path, "a", "errored", now=NOW - 100)  # evidence: x<1
    ids = [_not_holdout(), next(f"z{i}" for i in range(200) if not tr.is_holdout(f"z{i}")),
           _holdout()]
    for mid in ids:
        tr.decide_bid(0.5, "r", message_id=mid, runtime_dir=tmp_path, participant="a",
                      repo=None)
    tr.record_turn(tmp_path, "a", "errored", kind="open_todo", message_id=ids[0])
    tr.record_turn(tmp_path, "a", "clean", kind="open_todo", message_id=ids[1])
    tr.record_turn(tmp_path, "a", "timed_out", kind="open_todo", message_id=ids[2])
    tr.record_turn(tmp_path, "a", "errored", kind="mention")
    tr.record_turn(tmp_path, "a", "errored", kind="open_todo", message_id="never-bid")
    out = tr.report(tmp_path, "a")
    assert out["arms"]["routed"] == {"turns": 2, "failed": 1}
    assert out["arms"]["holdout"] == {"turns": 1, "failed": 1}
    assert out["predictive"]["low"] == {"turns": 3, "failed": 2}


# ── wired into the daemon ────────────────────────────────────────────────

_spec = importlib.util.spec_from_file_location(
    "reflexd_track", Path(__file__).resolve().parent.parent / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd_track"] = reflexd
_spec.loader.exec_module(reflexd)


def test_daemon_open_bid_carries_track_record(tmp_path, monkeypatch):
    monkeypatch.setattr(reflexd, "workspace_for", lambda room, **kw: None)
    cfg = reflexd.ReflexdConfig(relay_url="http://relay.test", participant_name="a-gemini",
                                api_key="k", runtime_dir=tmp_path)
    daemon = reflexd.Reflexd(cfg)
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    tr.record_turn(tmp_path, "a-gemini", "vendor_failure")
    tr.record_turn(tmp_path, "a-gemini", "vendor_failure")
    bids: list[dict[str, Any]] = []
    posts: list[dict[str, Any]] = []

    class BidRelay:
        async def submit_bid(self, **kw):
            bids.append(kw)
            return {}

        async def claim(self, *, room_id, message_id):
            return {"winner": "someone-else"}

        async def post_social_defer(self, **kw):  # pragma: no cover
            return {}

        async def post_reply(self, **kw):
            posts.append(kw)
            return {}

    monkeypatch.setattr(reflexd, "BID_WINDOW_SECONDS", 0.2)
    envelope = {"id": "f", "message_id": _not_holdout(), "room": "dev", "from_name": "arav",
                "content": "@open please fix the tests", "message_type": "chat"}
    asyncio.run(daemon.handle_room_message(BidRelay(), envelope))
    assert bids, "an @open must still bid"
    assert bids[0]["bid"] < 0.5
    assert "track x0." in bids[0]["reason"] and "0✓ 2✗/24h" in bids[0]["reason"]
    [logged] = tr.load_bids(tmp_path, "a-gemini")
    assert logged["harness"] == "gemini" and logged["arm"] == "routed"
    assert logged["scope"] == "relay.test|a|"
    [line] = posts
    assert line["message_type"] == "status" and line["room"] == "dev"
    assert line["content"].startswith("(quorus) adapted t1: @open bids x0.")
