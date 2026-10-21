"""Ordinary completion and no-op parking cannot release proof-owned holds."""

import asyncio
import json

import pytest

from quorus.runtime.handoff_capsule import build_capsule
from quorus.runtime.handoff_hold import PublicationHold
from quorus.runtime.handoff_state import Journal
from tests.test_handoff_capsule import arguments
from tests.test_unfinished_wakes import _daemon, _git, _Relay, _repo, _wake, reflexd


def _retained_hold(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    wt = reflexd.reflexd_wake.agent_worktree(repo, d.config.participant_name)
    (wt.path / "CHANGELOG.md").write_text("starter's unverified work\n")
    _git(wt.path, "commit", "-qam", "starter unfinished work")
    head = _git(wt.path, "rev-parse", "HEAD")
    args = arguments()
    args.update(config=None, checkpoint={"status": "none", "head": head})
    args["repo"].update(base=_git(repo, "rev-parse", "main"), head=head,
                        tree=_git(wt.path, "rev-parse", "HEAD^{tree}"))
    capsule = build_capsule(**args)
    journal = Journal(d.config.runtime_dir / "handoffs.json", "build", repo,
                      hold_dir=d._unfinished_dir)
    hold = PublicationHold(wt.path, journal, capsule)
    hold.acquire(wt.branch)
    return repo, d, wt, hold


def test_retained_hold_survives_clean_ordinary_turn_and_restart_sweep(monkeypatch, tmp_path):
    repo, d, wt, hold = _retained_hold(monkeypatch, tmp_path)
    before = _git(repo, "rev-parse", "main")
    marker = hold.path.read_bytes()
    relay = _wake(monkeypatch, d, reply="✅ unrelated clean task finished",
                  edit="unrelated clean task\n", msg="different-owner-request")
    assert _git(repo, "rev-parse", "main") == before
    assert hold.path.read_bytes() == marker
    assert reflexd.reflexd_unfinished.pending(d._unfinished_dir, wt.path)
    assert not any("published " in p["content"] for p in relay.posted)
    restarted = _daemon(monkeypatch, tmp_path, repo)
    asyncio.run(restarted._publish_sweep(_Relay()))
    assert _git(repo, "rev-parse", "main") == before
    assert hold.path.read_bytes() == marker
    # Keep both tasks' work for the owner, rather than silently reverting either.
    assert _git(wt.path, "rev-list", "--count", "main..HEAD") == "2"


def test_no_op_park_does_not_drop_a_handoff_hold(monkeypatch, tmp_path):
    _, d, wt, hold = _retained_hold(monkeypatch, tmp_path)
    # Work may have reached main meanwhile. The handoff/owner still owns this
    # marker; an unrelated no-op turn has no authority to release it.
    _git(wt.repo, "update-ref", "refs/heads/main", _git(wt.path, "rev-parse", "HEAD"))
    marker = hold.path.read_bytes()
    assert reflexd.reflexd_unfinished.park(d._unfinished_dir, wt,
                                         d.config.participant_name, "no changes") is None
    assert hold.path.read_bytes() == marker


@pytest.mark.parametrize("raw", ["broken", "[]", '{"handoff":null}', '{"handoff":"fp"}'])
def test_clear_fails_closed_on_proof_owned_or_corrupt_markers(monkeypatch, tmp_path, raw):
    _, d, wt, hold = _retained_hold(monkeypatch, tmp_path)
    hold.path.write_text(raw)
    with pytest.raises(OSError):
        reflexd.reflexd_unfinished.clear(d._unfinished_dir, wt)
    assert hold.path.read_text() == raw


def test_normal_finishing_turn_still_clears_an_ordinary_hold(monkeypatch, tmp_path):
    _, d, wt, hold = _retained_hold(monkeypatch, tmp_path)
    value = json.loads(hold.path.read_text())
    del value["handoff"]
    hold.path.write_text(json.dumps(value))
    reflexd.reflexd_unfinished.clear(d._unfinished_dir, wt)
    assert not hold.path.exists()
