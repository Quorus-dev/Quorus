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


def test_no_evidence_is_neutral_and_bounds_hold():
    assert tr.Track().multiplier == 1.0
    assert tr.Track(clean=500).multiplier == tr.MAX_MULT
    assert tr.Track(failed=500).multiplier == tr.MIN_MULT
    assert tr.Track(clean=1, failed=1).multiplier == 1.0
    assert tr.Track(clean=4, failed=1).multiplier > 1.0 > tr.Track(clean=1, failed=4).multiplier


def test_kept_commits_are_capped_so_volume_alone_cannot_max_a_bid():
    assert tr.Track(kept=500, failed=5).multiplier < tr.Track(kept=0, clean=500).multiplier
    assert tr.Track(kept=500).multiplier == tr.Track(kept=tr.LEDGER_KEPT_CAP).multiplier


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


def test_two_failures_down_weight_the_bid_with_the_evidence_shown(tmp_path):
    tr.record_turn(tmp_path, "a-gemini", "vendor_failure", now=NOW - 30)
    tr.record_turn(tmp_path, "a-gemini", "timed_out", now=NOW - 20)
    bid, reason = tr.adjust_bid(0.5, "open_capability_match", message_id=_not_holdout(),
                                runtime_dir=tmp_path, participant="a-gemini", repo=None)
    assert bid < 0.5
    assert reason.startswith("open_capability_match+track x0.")
    assert "0✓ 2✗/24h" in reason and "1 timeout" in reason


def test_holdout_and_off_switch_keep_raw_bids(tmp_path, monkeypatch):
    tr.record_turn(tmp_path, "a-gemini", "errored", now=None)
    raw = dict(runtime_dir=tmp_path, participant="a-gemini", repo=None)
    assert tr.adjust_bid(0.5, "r", message_id=_holdout(), **raw) == (0.5, "r+holdout")
    monkeypatch.setenv("QUORUS_TRACK_BIDS", "0")
    assert tr.adjust_bid(0.5, "r", message_id=_not_holdout(), **raw) == (0.5, "r")


def test_no_evidence_leaves_bid_and_reason_untouched(tmp_path):
    for mid in (_holdout(), _not_holdout()):
        assert tr.adjust_bid(0.3, "open_any_agent", message_id=mid, runtime_dir=tmp_path,
                             participant="nobody", repo=None) == (0.3, "open_any_agent")


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
    assert "ledger 1 kept 1 reverted" in track.reason()


def test_report_splits_open_wins_by_arm(tmp_path):
    tr.record_turn(tmp_path, "a", "errored", kind="open_todo", holdout=False, now=NOW)
    tr.record_turn(tmp_path, "a", "clean", kind="open_todo", holdout=False, now=NOW)
    tr.record_turn(tmp_path, "a", "timed_out", kind="open_todo", holdout=True, now=NOW)
    tr.record_turn(tmp_path, "a", "errored", kind="mention", now=NOW)
    assert tr.report(tmp_path, "a", NOW) == {
        "routed": {"turns": 2, "failed": 1}, "holdout": {"turns": 1, "failed": 1}}


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
    tr.record_turn(tmp_path, "a-gemini", "vendor_failure")
    tr.record_turn(tmp_path, "a-gemini", "vendor_failure")
    bids: list[dict[str, Any]] = []

    class BidRelay:
        async def submit_bid(self, **kw):
            bids.append(kw)
            return {}

        async def claim(self, *, room_id, message_id):
            return {"winner": "someone-else"}

        async def post_social_defer(self, **kw):  # pragma: no cover
            return {}

    monkeypatch.setattr(reflexd, "BID_WINDOW_SECONDS", 0.2)
    envelope = {"id": "f", "message_id": _not_holdout(), "room": "dev", "from_name": "arav",
                "content": "@open please fix the tests", "message_type": "chat"}
    asyncio.run(daemon.handle_room_message(BidRelay(), envelope))
    assert bids, "an @open must still bid"
    assert bids[0]["bid"] < 0.5
    assert "track x0." in bids[0]["reason"] and "0✓ 2✗/24h" in bids[0]["reason"]
