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
    ("[reflexd] gemini not installed on this host", False, "", "errored"),  # live check
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


def _turn(runtime, outcome, scope=SCOPE):
    tr.record_turn(runtime, "a-gemini", outcome, scope=scope)


def _two_failures(tmp_path):
    _turn(tmp_path, "vendor_failure")
    _turn(tmp_path, "timed_out")
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
    assert d.announce.startswith("(quorus) a.t1 (shadow, not applied): would set @open bids x0.")
    assert "1 vendor fail, 1 timeout, 0 clean/24h" in d.announce
    assert d.announce.endswith("undo: quorus tune undo a.t1")  # id names the owner (BUG 18)
    [logged] = tr.load_bids(tmp_path, "a-gemini")
    assert logged["arm"] == "shadow" and logged["raw"] == 0.5 and logged["applies"]
    assert logged["message_id"] == mid and logged["harness"] == "gemini"
    assert logged["scope"] == SCOPE


def test_on_applies_except_on_holdout_and_announces_once(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    kw = _two_failures(tmp_path)
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert d.bid < 0.5 and d.reason.startswith("r+track x0.")
    assert d.announce.startswith("(quorus) adapted a.t1: @open bids x0.")
    d = tr.decide_bid(0.5, "r", message_id=_holdout(), **kw)
    assert d.bid == 0.5 and d.reason.startswith("r+track(holdout) x0.") and d.announce is None
    assert [b["arm"] for b in tr.load_bids(tmp_path, "a-gemini")] == ["routed", "holdout"]


def test_hysteresis_and_cooldown_stop_flapping(tmp_path, monkeypatch):
    from quorus import tuning

    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    kw = _two_failures(tmp_path)
    for _ in range(2):
        _turn(tmp_path, "errored")  # 0✓ 4✗: x0.67
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw).announce
    for _ in range(2):
        _turn(tmp_path, "clean")  # 2✓ 4✗: x0.88, not past x0.9
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert 0.8 < tr.track_for(tmp_path, "a-gemini", None, scope=SCOPE).multiplier < 0.9
    assert d.bid < 0.5 and d.announce is None
    for _ in range(2):
        _turn(tmp_path, "clean")  # 4✓ 4✗: x1.0, it ends
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert d.bid == 0.5 and tuning.adaptations()[0].state == "retired"
    for _ in range(8):
        _turn(tmp_path, "errored")  # past x0.8 again, inside 1h
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert d.bid == 0.5 and d.announce is None and len(tuning.adaptations()) == 1


def test_undo_holds_until_redo_and_tune_off_is_immediate(tmp_path, monkeypatch):
    from quorus import tuning

    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    kw = _two_failures(tmp_path)
    tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert tuning.undo("a.t1") is not None
    for _ in range(5):
        _turn(tmp_path, "errored")
    d = tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    assert d.bid == 0.5 and d.announce is None and len(tuning.adaptations()) == 1
    assert tuning.redo("a.t1") is not None
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw).bid < 0.5
    tuning.set_enabled(False)
    assert tr.mode() == "off"
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw) == tr.BidDecision(0.5, "r")


def test_scope_keeps_adaptations_apart(tmp_path, monkeypatch):
    from quorus import tuning

    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    kw = _two_failures(tmp_path)
    tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw)
    tuning.undo("a.t1")
    other = {**kw, "scope": "other-relay|a|/repo"}
    _turn(tmp_path, "vendor_failure", scope=other["scope"])
    _turn(tmp_path, "timed_out", scope=other["scope"])
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **other).announce
    assert [a.scope for a in tuning.adaptations()] == [SCOPE, "other-relay|a|/repo"]


def test_off_computes_and_logs_nothing(tmp_path, monkeypatch):
    kw = _two_failures(tmp_path)
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "off")
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw) == tr.BidDecision(0.5, "r")
    _turn(tmp_path, "errored")
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
    pids = [tr.decide_bid(0.5, "r", message_id=mid, runtime_dir=tmp_path, participant="a",
                          repo=None).prediction_id for mid in ids]
    for mid, pid, outcome in zip(ids, pids, ("errored", "clean", "timed_out")):
        tr.record_turn(tmp_path, "a", outcome, kind="open_todo", message_id=mid,
                       prediction_id=pid)
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
    daemon.adapter.model = "gemini-3.5-flash"
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    _turn(tmp_path, "vendor_failure", scope="http://relay.test|a|")
    _turn(tmp_path, "vendor_failure", scope="http://relay.test|a|")
    bids: list[dict[str, Any]] = []
    posts: list[dict[str, Any]] = []

    class BidRelay:
        async def submit_bid(self, **kw):
            bids.append(kw)
            return {}

        async def claim(self, *, room_id, message_id):
            return {"claimed": True, "winner": "x-codex", "bid": 0.5,
                    "candidates": ["a-gemini", "x-codex"]}

        async def post_social_defer(self, **kw):  # pragma: no cover
            return {}

        async def post_reply(self, **kw):
            posts.append(kw)
            return {}

        async def fetch_members(self, *, room):
            return ["a", "a-gemini"]  # the owner's own room: the line stays a status post

    monkeypatch.setattr(reflexd, "BID_WINDOW_SECONDS", 0.2)
    envelope = {"id": "f", "message_id": _not_holdout(), "room": "dev", "from_name": "arav",
                "content": "@open please fix the tests", "message_type": "chat"}
    asyncio.run(daemon.handle_room_message(BidRelay(), envelope))
    assert bids, "an @open must still bid"
    assert bids[0]["bid"] < 0.5
    assert "track x0." in bids[0]["reason"] and "0✓ 2✗/24h" in bids[0]["reason"]
    [logged] = tr.load_bids(tmp_path, "a-gemini")
    assert logged["harness"] == "gemini" and logged["arm"] == "routed"
    assert logged["scope"] == "http://relay.test|a|" and logged["model"] == "gemini-3.5-flash"
    # the wake that runs this @open takes this exact snapshot (codex, f504e800)
    assert daemon._predictions[envelope["message_id"]] == logged["prediction_id"]
    [auction] = tr.load_claims(tmp_path, "a-gemini", scope="http://relay.test|a|")
    assert auction["winner"] == "x-codex" and auction["candidates"] == ["a-gemini", "x-codex"]
    assert auction["message_id"] == envelope["message_id"] and auction["winning_bid"] == 0.5
    [line] = posts
    assert line["message_type"] == "status" and line["room"] == "dev"
    assert line["content"].startswith("(quorus) adapted a.t1: @open bids x0.")


def test_failed_harness_exits_and_slow_starts_are_not_clean():
    # Codex's repro: a non-zero exit keeps the errored sentinel.
    reply = reflexd.HarnessExitFailure(2)
    assert tr.classify_turn(str(reply).strip(), vendor_failure=False,
                            failure_reason="") == "errored"
    stall = reflexd.SlowStart("opencode", 120)
    assert tr.classify_turn(stall, vendor_failure=False, failure_reason="",
                            slow_start=isinstance(stall, reflexd.SlowStart)) == "timed_out"


def test_later_bid_cannot_relabel_an_earlier_outcome(tmp_path):
    # Codex's repro: a retry's bid on the same message must not rewrite the
    # prediction that was made before the first outcome.
    def bid(ts, mult, pid):
        tr._append(tmp_path, "a", "bids", {"ts": ts, "message_id": "same-task",
                                           "prediction_id": pid, "arm": "shadow",
                                           "multiplier": mult}, NOW)

    bid(NOW - 3, 0.6, "p1")
    tr.record_turn(tmp_path, "a", "errored", kind="open_todo", message_id="same-task",
                   prediction_id="p1", now=NOW - 2)
    bid(NOW - 1, 1.3, "p2")
    tr.record_turn(tmp_path, "a", "clean", kind="open_todo", message_id="same-task",
                   prediction_id="p2", now=NOW)
    out = tr.report(tmp_path, "a", NOW)
    assert out["predictive"]["low"] == {"turns": 1, "failed": 1}
    assert out["predictive"]["high"] == {"turns": 1, "failed": 0}



def test_unreadable_tuning_state_leaves_bids_raw(tmp_path, monkeypatch):
    # Codex's repro: a damaged file after `tune off` must not mean "on".
    from quorus import tuning

    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    tuning.set_enabled(False)
    assert tr.mode() == "off"
    tuning.path().write_text("{invalid")
    assert tr.mode() == "off" and tuning.is_invalid()
    kw = _two_failures(tmp_path)
    assert tr.decide_bid(0.5, "r", message_id=_not_holdout(), **kw) == tr.BidDecision(0.5, "r")
    assert tuning.path().read_text() == "{invalid"  # nothing written over it
    tuning.set_enabled(True)  # the user's explicit `tune on` starts a fresh file
    assert not tuning.is_invalid() and tr.mode() == "on"


def test_other_scope_does_not_inherit_failure_evidence(tmp_path, monkeypatch):
    # Codex's repro: evidence from one relay|owner|repo stays there.
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "on")
    _turn(tmp_path, "vendor_failure")
    _turn(tmp_path, "vendor_failure")
    fresh = dict(runtime_dir=tmp_path, participant="a-gemini", repo=None,
                 scope="fresh-relay|a|/fresh-repo")
    d = tr.decide_bid(0.5, "open", message_id=_not_holdout(), **fresh)
    assert d.bid == 0.5 and d.announce is None
    assert tr.track_for(tmp_path, "a-gemini", None, scope=SCOPE).failed == 2


def test_report_joins_only_within_a_scope(tmp_path):
    for scope, mult in (("A", 0.6), ("B", 1.3)):
        tr._append(tmp_path, "a", "bids", {"ts": NOW - 5, "message_id": "m", "arm": "shadow",
                                           "prediction_id": f"p{scope}", "multiplier": mult,
                                           "scope": scope}, NOW)
    tr.record_turn(tmp_path, "a", "errored", kind="open_todo", message_id="m", scope="A",
                   prediction_id="pA", now=NOW)
    assert tr.report(tmp_path, "a", NOW)["predictive"]["low"] == {"turns": 1, "failed": 1}
    assert tr.report(tmp_path, "a", NOW, scope="B")["predictive"]["low"]["turns"] == 0



def test_turns_and_bids_record_the_model(tmp_path):
    tr.record_turn(tmp_path, "a-gemini", "timed_out", scope=SCOPE, model="gemini-3.5-flash")
    tr.record_turn(tmp_path, "a-gemini", "timed_out", scope=SCOPE, model="gemini-3.5-flash")
    tr.decide_bid(0.5, "r", message_id=_not_holdout(), runtime_dir=tmp_path,
                  participant="a-gemini", repo=None, scope=SCOPE, model="gemini-3.5-flash")
    assert {t["model"] for t in tr.load_turns(tmp_path, "a-gemini")} == {"gemini-3.5-flash"}
    assert tr.load_bids(tmp_path, "a-gemini")[0]["model"] == "gemini-3.5-flash"


def test_record_claim_keeps_only_announced_results(tmp_path, monkeypatch):
    tr.record_claim(tmp_path, "a", message_id="m", scope="S", claim={"winner": "b"})
    tr.record_claim(tmp_path, "a", message_id="m", scope="S",
                    claim={"claimed": True, "winner": "b", "bid": "x", "candidates": "nope"})
    [row] = tr.load_claims(tmp_path, "a", scope="S")
    assert row["winner"] == "b" and row["winning_bid"] is None and row["candidates"] == []
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "off")
    tr.record_claim(tmp_path, "a", message_id="m2", scope="S",
                    claim={"claimed": True, "winner": "b"})
    assert len(tr.load_claims(tmp_path, "a")) == 1



def test_scope_keeps_relays_on_one_host_apart():
    def scope(url):
        cfg = reflexd.ReflexdConfig(relay_url=url, participant_name="a-gemini", api_key="k")
        return reflexd.Reflexd(cfg)._track_scope(None)

    assert scope("https://h.example/team-a") != scope("https://h.example/team-b")
    assert scope("https://h.example/team-a/") == scope("https://h.example/team-a")
    assert scope("http://h.example") != scope("https://h.example")
    assert scope("https://h.example/team-a") == "https://h.example/team-a|a|"
