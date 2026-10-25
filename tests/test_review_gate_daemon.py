"""Review gate slice 3a: a reviewed repo's turns and sweeps hold, never publish
(docs/plans/review-gate.md, Codex GO on a1f25392)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess as sp
import sys
from pathlib import Path
from typing import Any

import pytest

from quorus.runtime import review_gate as rg

_ROOT = Path(__file__).resolve().parent.parent
if "reflexd" in sys.modules:
    reflexd = sys.modules["reflexd"]
else:
    _spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
    assert _spec is not None and _spec.loader is not None
    reflexd = importlib.util.module_from_spec(_spec)
    sys.modules["reflexd"] = reflexd
    _spec.loader.exec_module(reflexd)
wake = reflexd.reflexd_wake
G = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def _git(cwd: Path, *args: str) -> str:
    return sp.run([*G, *args], cwd=cwd, check=True, capture_output=True,
                  text=True).stdout.strip()


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []

    async def fetch_history(self, **_kw: Any) -> list[dict[str, Any]]:
        return []

    async def post_reply(self, **kw: Any) -> None:
        self.posted.append(kw)


class _NeverCalled:
    async def run(self, *a: Any, **k: Any) -> str:
        raise AssertionError("no extra wake needed")


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(cfg))
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    wt = wake.agent_worktree(repo, "b-claude")
    for name in ("one.txt", "two.txt"):
        (wt.path / name).write_text(name)
        _git(wt.path, "add", name)
        _git(wt.path, "commit", "-q", "-m", f"add {name}")
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps({"r": str(repo), "side": {"path": str(repo)}}))
    monkeypatch.setattr(reflexd, "ROOM_BINDINGS_PATH", bindings)
    cfg_obj = reflexd.ReflexdConfig(relay_url="http://test", api_key="k",
                                    participant_name="b-claude",
                                    runtime_dir=tmp_path / "rt", log_path=tmp_path / "l.log")
    d = reflexd.Reflexd(cfg_obj)
    d.adapter = _NeverCalled()
    return d, repo, wt, cfg


def _pin(cfg: Path, room: str = "r", **kw: str) -> None:
    rg.save_reviewer(room, "rev-codex", path=cfg / "room-reviewers.json",
                     **({"tenant": "t1", "claim_id": "c-rev"} | kw))


def _publish(d, wt, room: str = "r", summary: str = "") -> str | None:
    return asyncio.run(d._publish_with_resolution(
        wt, harness="claude", resume=None, on_session=lambda _s: None, timeout_s=None,
        max_turns=None, writable_root=None, summary=summary, room=room))


def test_hold_mode_rebases_but_never_moves_main(env):
    _d, repo, wt, _cfg = env
    _git(repo, "commit", "-q", "--allow-empty", "-m", "main moved")
    main = _git(repo, "rev-parse", "main")
    status = wake.publish_worktree(wt, "b-claude", hold=True)
    tip, count = status[len(wake.REVIEW_READY):].split()
    assert _git(repo, "rev-parse", "main") == main
    assert tip == _git(repo, "rev-parse", wt.branch) and count == "2"
    assert _git(repo, "merge-base", "--is-ancestor", main, tip) == ""  # rebased onto it


def test_reviewed_room_holds_the_turn(env):
    d, repo, wt, cfg = env
    _pin(cfg)
    main = _git(repo, "rev-parse", "main")
    status = _publish(d, wt)
    tip = _git(repo, "rev-parse", wt.branch)
    assert status == (f"(quorus) awaiting @rev-codex's review of {tip[:12]} "
                      "(2 commit(s)); not on main")
    assert _git(repo, "rev-parse", "main") == main
    marker = rg.load_marker(d.config.runtime_dir, repo, wt.branch)
    assert marker is not None
    assert (marker["tip"], marker["base"], marker["count"], marker["room"],
            marker["reviewer"]) == (tip, main, 2, "r", "rev-codex")


def test_uncommitted_work_is_committed_then_held(env):
    d, repo, wt, cfg = env
    _pin(cfg)
    (wt.path / "three.txt").write_text("3")
    main = _git(repo, "rev-parse", "main")
    status = _publish(d, wt, summary="✅ add three")
    assert "awaiting @rev-codex's review" in status and "(3 commit(s))" in status
    assert _git(repo, "rev-parse", "main") == main
    assert not wake.worktree_dirty(wt)


def test_unreviewed_repo_publishes_as_before(env):
    d, repo, wt, _cfg = env
    status = _publish(d, wt)
    assert "published 2 commit(s)" in status
    assert _git(repo, "rev-parse", "main") == _git(repo, "rev-parse", wt.branch)


def test_a_reviewed_room_gates_every_room_on_its_repo(env):
    d, repo, wt, cfg = env
    _pin(cfg, room="side")  # the turn runs in "r"; "side" shares the repo
    main = _git(repo, "rev-parse", "main")
    status = _publish(d, wt, room="r")
    assert "awaiting @rev-codex's review" in status
    assert _git(repo, "rev-parse", "main") == main
    assert rg.load_marker(d.config.runtime_dir, repo, wt.branch)["room"] == "side"


def test_unpinned_reviewer_holds_with_an_honest_status(env):
    d, repo, wt, cfg = env
    rg.save_reviewer("r", "rev-codex", path=cfg / "room-reviewers.json")
    main = _git(repo, "rev-parse", "main")
    status = _publish(d, wt)
    assert "can't be authenticated on this relay; owner override only" in status
    assert _git(repo, "rev-parse", "main") == main


def test_unreadable_reviewers_file_holds(env):
    d, repo, wt, cfg = env
    (cfg / "room-reviewers.json").write_text("{not json")
    main = _git(repo, "rev-parse", "main")
    status = _publish(d, wt)
    assert "owner override only" in status and _git(repo, "rev-parse", "main") == main


def test_rehold_keeps_the_review_clock_until_the_tip_changes(env):
    d, repo, wt, cfg = env
    _pin(cfg)
    _publish(d, wt)
    first = rg.load_marker(d.config.runtime_dir, repo, wt.branch)
    _publish(d, wt)
    assert rg.load_marker(d.config.runtime_dir, repo, wt.branch)["since"] == first["since"]
    (wt.path / "four.txt").write_text("4")
    _publish(d, wt, summary="✅ add four")
    again = rg.load_marker(d.config.runtime_dir, repo, wt.branch)
    assert again["tip"] != first["tip"] and again["since"] >= first["since"]


def test_restart_sweep_holds_instead_of_publishing(env):
    d, repo, wt, cfg = env
    _pin(cfg)
    main = _git(repo, "rev-parse", "main")
    relay = _Relay()
    asyncio.run(d._publish_sweep(relay))
    assert _git(repo, "rev-parse", "main") == main
    assert any("awaiting @rev-codex's review" in p["content"] for p in relay.posted)
    assert rg.load_marker(d.config.runtime_dir, repo, wt.branch) is not None


def test_restart_sweep_without_reviewer_still_publishes(env):
    d, repo, wt, _cfg = env
    relay = _Relay()
    asyncio.run(d._publish_sweep(relay))
    assert _git(repo, "rev-parse", "main") == _git(repo, "rev-parse", wt.branch)
    assert any("published 2 commit(s)" in p["content"] for p in relay.posted)


# ── slice 3b: the reviewer's GO publishes the exact held tip ─────────────


class _Room(_Relay):
    def __init__(self, history: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.history = history or []

    async def fetch_history(self, **_kw: Any) -> list[dict[str, Any]]:
        return list(self.history)


def _say(text: str, *, mid: str = "g1", auth: str = "key", claim: str = "c-rev",
         sender: str = "rev-codex", ts: str = "2026-10-25T08:00:00") -> dict[str, Any]:
    msg = {"id": mid, "from_name": sender, "content": text, "timestamp": ts,
           "sender_auth": auth, "sender_tenant": "t1"}
    if auth == "key":
        msg["sender_claim_id"] = claim
    return msg


def _held(env) -> tuple[Any, Path, Any, str, str]:
    d, repo, wt, cfg = env
    _pin(cfg)
    main = _git(repo, "rev-parse", "main")
    _publish(d, wt)
    return d, repo, wt, main, _git(repo, "rev-parse", wt.branch)


def _tick(d, relay, room: str = "r") -> None:
    asyncio.run(d._review_tick(relay, room))


def test_pinned_go_publishes_the_held_tip_once(env):
    d, repo, wt, _main, tip = _held(env)
    relay = _Room([_say(f"@b-claude ✅ GO on `{tip[:8]}`")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == tip
    assert rg.load_marker(d.config.runtime_dir, repo, wt.branch) is None
    [note] = [p["content"] for p in relay.posted]
    assert note.startswith(f"(quorus) published 2 commit(s) from {wt.branch} to main at ")
    assert note.endswith("; reviewed by @rev-codex (GO g1)")
    notes = _git(repo, "notes", "--ref=quorus", "show", "main")
    assert '"reviewed_by": "rev-codex"' in notes and '"review_msg": "g1"' in notes
    _tick(d, relay)  # the same GO again: nothing more to say
    assert len(relay.posted) == 1


def test_publish_note_still_parses_for_the_ledger(env):
    from quorus import ledger
    d, repo, _wt, _main, tip = _held(env)
    relay = _Room([_say(f"✅ GO on {tip[:8]}")])
    _tick(d, relay)
    assert ledger._PUBLISHED.search(relay.posted[0]["content"])


@pytest.mark.parametrize("msg", [
    {"auth": "secret"},                 # shared-secret bearer under the reviewer's name
    {"claim": "c-other"},               # a re-claimed name
    {"sender": "someone-else"},         # another member
])
def test_unpinned_go_publishes_nothing(env, msg):
    d, repo, _wt, main, tip = _held(env)
    relay = _Room([_say(f"✅ GO on {tip[:8]}", **msg)])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main and relay.posted == []


def test_go_on_an_older_commit_publishes_nothing(env):
    d, repo, wt, main, _tip = _held(env)
    first = _git(repo, "rev-list", "--reverse", f"main..{wt.branch}").split()[0]
    relay = _Room([_say(f"✅ GO on {first[:8]}")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main


def test_block_holds_and_says_so_once(env):
    d, repo, _wt, main, tip = _held(env)
    relay = _Room([_say(f"✅ GO on {tip[:8]}", ts="2026-10-25T08:00:00"),
                   _say(f"/disagree blocking on `{tip[:8]}`: no test", mid="b1",
                        ts="2026-10-25T08:01:00")])
    _tick(d, relay)
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main
    assert [p["content"] for p in relay.posted] == [
        f"(quorus) held: @rev-codex /disagree blocking on {tip[:12]}; not on main"]
    relay.history.append(_say(f"✅ GO on {tip[:8]}", mid="g2", ts="2026-10-25T08:02:00"))
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == tip


def test_commit_after_the_hold_is_never_published_by_the_go(env):
    d, repo, wt, main, tip = _held(env)
    (wt.path / "late.txt").write_text("late")
    _git(wt.path, "add", "late.txt")
    _git(wt.path, "commit", "-q", "-m", "late")
    relay = _Room([_say(f"✅ GO on {tip[:8]}")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main  # the branch moved past the GO'd tip
    assert relay.posted == []


def test_main_moved_after_the_go_rebases_and_asks_again(env):
    d, repo, wt, _main, tip = _held(env)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "someone else landed")
    moved = _git(repo, "rev-parse", "main")
    relay = _Room([_say(f"✅ GO on {tip[:8]}")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == moved
    new = rg.load_marker(d.config.runtime_dir, repo, wt.branch)
    assert new["tip"] != tip and new["base"] == moved
    [status] = [p["content"] for p in relay.posted]
    assert f"after @rev-codex's GO on {tip[:12]}" in status
    assert f"rebased to {new['tip'][:12]}, which needs a fresh review" in status
    assert "range-diff" in status and status.endswith("not on main")


def test_dirty_main_checkout_says_why_once(env):
    d, repo, _wt, main, tip = _held(env)
    (repo / "human.txt").write_text("x")
    _git(repo, "add", "human.txt")
    relay = _Room([_say(f"✅ GO on {tip[:8]}")])
    _tick(d, relay)
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main
    [status] = [p["content"] for p in relay.posted]
    assert f"@rev-codex's GO on {tip[:12]} not published" in status
    assert "uncommitted changes" in status


def test_removed_reviewer_drops_the_marker_without_publishing(env):
    d, repo, wt, main, tip = _held(env)
    _, _, _, cfg = env
    rg.clear_reviewer("r", cfg / "room-reviewers.json")
    _tick(d, _Room([_say(f"✅ GO on {tip[:8]}")]))
    assert _git(repo, "rev-parse", "main") == main
    assert rg.load_marker(d.config.runtime_dir, repo, wt.branch) is None


def test_history_fetch_failure_publishes_nothing(env):
    d, repo, _wt, main, _tip = _held(env)

    class Down(_Relay):
        async def fetch_history(self, **_kw: Any) -> list[dict[str, Any]]:
            raise RuntimeError("relay down")

    relay = Down()
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main and relay.posted == []


def test_review_sweep_finds_a_go_the_live_path_missed(env):
    d, repo, _wt, _main, tip = _held(env)
    relay = _Room([_say(f"✅ GO on {tip[:8]}")])
    asyncio.run(d._review_sweep(relay))
    assert _git(repo, "rev-parse", "main") == tip


def test_a_room_message_schedules_the_tick(env):
    d, repo, _wt, _main, tip = _held(env)
    relay = _Room([_say(f"✅ GO on {tip[:8]}")])

    async def go() -> None:
        await d._dispatch_event(relay, "message", {
            "message_id": "g1", "room": "r", "from_name": "rev-codex",
            "content": f"✅ GO on {tip[:8]}", "message_type": "status"})
        await asyncio.gather(*list(d._bg_tasks), return_exceptions=True)

    asyncio.run(go())
    assert _git(repo, "rev-parse", "main") == tip


# ── shared-repo gating fails closed (codex, review of 29b0b1fa) ─────────


@pytest.mark.parametrize("bad", ["{not json", "[]", None, '{"side": 7}', '{"side": {"path": 3}}'])
def test_sibling_reviewer_with_unreadable_bindings_holds(env, monkeypatch, bad):
    d, repo, wt, cfg = env
    _pin(cfg, room="side")  # the turn runs in "r"; only "side" has a reviewer
    path = reflexd.ROOM_BINDINGS_PATH
    if bad is None:
        path.unlink()
    else:
        path.write_text(bad)
    main = _git(repo, "rev-parse", "main")
    gate = reflexd._review_gate_for("r", repo)
    assert gate is not None and not rg.pinned(gate[1])
    status = asyncio.run(d._publish_with_resolution(
        wt, harness="claude", resume=None, on_session=lambda _s: None, timeout_s=None,
        max_turns=None, writable_root=None, room="r"))
    assert "room bindings can't be read" in status and "owner override only" in status
    assert _git(repo, "rev-parse", "main") == main


def test_unreadable_reviewers_file_holds_every_room(env):
    d, repo, wt, cfg = env
    (cfg / "room-reviewers.json").write_text("{not json")
    assert reflexd._review_gate_for("anything", repo) is not None
    main = _git(repo, "rev-parse", "main")
    _publish(d, wt, room="r")
    assert _git(repo, "rev-parse", "main") == main


def test_no_reviewers_anywhere_publishes_even_with_bad_bindings(env):
    d, repo, wt, _cfg = env
    reflexd.ROOM_BINDINGS_PATH.write_text("{not json")
    assert reflexd._review_gate_for("r", repo) is None


def test_reviewed_room_bound_elsewhere_does_not_gate_this_repo(env, tmp_path):
    d, repo, wt, cfg = env
    other = tmp_path / "other"
    other.mkdir()
    reflexd.ROOM_BINDINGS_PATH.write_text(json.dumps({"r": str(repo), "elsewhere": str(other)}))
    _pin(cfg, room="elsewhere")
    assert reflexd._review_gate_for("r", repo) is None
    assert "published 2 commit(s)" in _publish(d, wt, room="r")


def test_reviewed_room_not_bound_at_all_does_not_gate(env):
    d, repo, _wt, cfg = env
    _pin(cfg, room="unbound")
    assert reflexd._review_gate_for("r", repo) is None


# ── the decision is re-read under the repo lock (codex, review of 4bc515ba) ──


class _Changing(_Room):
    """The first *keep* history reads show only the GO; later reads add more."""

    def __init__(self, go: dict[str, Any], later: list[dict[str, Any]], keep: int) -> None:
        super().__init__([go])
        self.later, self.keep, self.reads = later, keep, 0

    async def fetch_history(self, **_kw: Any) -> list[dict[str, Any]]:
        self.reads += 1
        return list(self.history) + (self.later if self.reads > self.keep else [])


def test_block_seen_by_the_guards_read_wins(env):
    d, repo, _wt, main, tip = _held(env)
    block = _say(f"/disagree blocking on {tip[:8]}", mid="b1", ts="2026-10-25T08:05:00")
    relay = _Changing(_say(f"✅ GO on {tip[:8]}"), [block], keep=1)
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main
    assert relay.reads >= 3  # decide, the publish guard, and the re-read under the lock
    assert [p["content"] for p in relay.posted] == [
        f"(quorus) held: @rev-codex /disagree blocking on {tip[:12]}; not on main"]


def test_block_that_lands_while_waiting_for_the_lock_wins(env):
    import threading

    from quorus.runtime import review_publish as rp
    d, repo, _wt, main, tip = _held(env)
    relay = _Room([_say(f"✅ GO on {tip[:8]}")])
    taken, done = threading.Event(), threading.Event()

    def hold_lock() -> None:
        with rp.repo_lock(d.config.runtime_dir, repo):
            taken.set()
            done.wait(10)

    t = threading.Thread(target=hold_lock)
    t.start()
    taken.wait(5)

    async def go() -> None:
        tick = asyncio.create_task(d._review_tick(relay, "r"))
        await asyncio.sleep(0.5)  # the tick is now waiting for the lock
        relay.history.append(_say(f"/disagree blocking on {tip[:8]}", mid="b1",
                                  ts="2026-10-25T08:05:00"))
        done.set()
        await tick

    try:
        asyncio.run(go())
    finally:
        done.set()
        t.join()
    assert _git(repo, "rev-parse", "main") == main
    assert any("/disagree blocking" in p["content"] for p in relay.posted)


def test_retraction_after_the_first_read_holds(env):
    d, repo, _wt, main, tip = _held(env)
    retract = _say(f"GO on {tip[:8]}; correction: no GO yet", mid="r1",
                   ts="2026-10-25T08:05:00")
    relay = _Changing(_say(f"✅ GO on {tip[:8]}"), [retract], keep=2)
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main


def test_room_unreadable_under_the_lock_holds(env):
    d, repo, _wt, main, tip = _held(env)

    class Flaky(_Room):
        reads = 0

        async def fetch_history(self, **_kw: Any) -> list[dict[str, Any]]:
            self.reads += 1
            if self.reads > 2:
                raise RuntimeError("relay down")
            return list(self.history)

    relay = Flaky([_say(f"✅ GO on {tip[:8]}")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main
    [status] = [p["content"] for p in relay.posted]
    assert "couldn't be re-read before publishing" in status


# ── slice 3c: one ⚠ on timeout, and the owner's override ─────────────────

OWNER_PIN = {"tenant": "t1", "claim_id": "c-owner"}


def _owner_says(text: str, *, mid: str = "o1", claim: str = "c-owner", auth: str = "key",
                ts: str = "2026-10-25T08:10:00") -> dict[str, Any]:
    return _say(text, mid=mid, auth=auth, claim=claim, sender="b", ts=ts)


def _age(d, repo, wt, seconds: float) -> None:
    import time as _t
    rg.update_marker(d.config.runtime_dir, repo, wt.branch, since=_t.time() - seconds)


def test_timeout_posts_one_warning_and_publishes_nothing(env, monkeypatch):
    d, repo, wt, main, tip = _held(env)
    alerts: list[str] = []
    monkeypatch.setattr(d, "_alert_owner", lambda room, text, *a, **k: alerts.append(text))
    _age(d, repo, wt, 21 * 60)
    relay = _Room([])
    _tick(d, relay)
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main
    [note] = [p["content"] for p in relay.posted]
    assert note.startswith(f"(quorus) ⚠ no review of {tip[:12]} from @rev-codex after 21 min")
    assert "still not on main" in note and "@b can override from this machine" in note
    assert len(alerts) == 1


def test_no_warning_before_the_timeout(env):
    d, repo, wt, _main, _tip = _held(env)
    _age(d, repo, wt, 60)
    relay = _Room([])
    _tick(d, relay)
    assert relay.posted == []


def test_timeout_is_configurable(env, monkeypatch):
    d, repo, wt, _main, _tip = _held(env)
    monkeypatch.setenv("QUORUS_REVIEW_TIMEOUT_S", "120")
    _age(d, repo, wt, 180)
    relay = _Room([])
    _tick(d, relay)
    assert "⚠ no review" in relay.posted[0]["content"]


def test_pinned_owner_override_publishes_unreviewed(env, monkeypatch):
    d, repo, wt, _main, tip = _held(env)
    monkeypatch.setattr(reflexd._owner_pin, "owner_pin", lambda _p: OWNER_PIN)
    _age(d, repo, wt, 21 * 60)
    warn = _Room([])
    _tick(d, warn)
    assert f"@b can say `publish {tip[:12]} unreviewed`" in warn.posted[0]["content"]
    relay = _Room([_owner_says(f"@b-claude publish {tip[:12]} unreviewed")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == tip
    [note] = [p["content"] for p in relay.posted]
    assert note.endswith("; unreviewed (owner override by @b o1)")
    notes = _git(repo, "notes", "--ref=quorus", "show", "main")
    assert '"override_by": "b"' in notes and '"reviewed_by": null' in notes


def test_override_beats_a_reviewer_block(env, monkeypatch):
    d, repo, _wt, _main, tip = _held(env)
    monkeypatch.setattr(reflexd._owner_pin, "owner_pin", lambda _p: OWNER_PIN)
    relay = _Room([_say(f"/disagree blocking on {tip[:8]}", mid="b1"),
                   _owner_says(f"publish {tip[:12]} unreviewed")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == tip


@pytest.mark.parametrize("pin, msg", [
    (None, {}),                                     # no owner pin: no in-room override
    (OWNER_PIN, {"auth": "secret"}),                # the owner's name via the relay secret
    (OWNER_PIN, {"claim": "c-other"}),              # someone else's key under the name
])
def test_unauthenticated_override_publishes_nothing(env, monkeypatch, pin, msg):
    d, repo, _wt, main, tip = _held(env)
    monkeypatch.setattr(reflexd._owner_pin, "owner_pin", lambda _p: pin)
    relay = _Room([_owner_says(f"publish {tip[:12]} unreviewed", **msg)])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main


@pytest.mark.parametrize("text", [
    "publish {sha} unreviewed — overseer",          # an agent signing as the owner
    "don't publish {sha} unreviewed",               # not the command
    "should I publish {sha} unreviewed?",
    "> publish {sha} unreviewed",                   # quoting
    "publish {other} unreviewed",                   # another commit
])
def test_not_an_override(env, monkeypatch, text):
    d, repo, _wt, main, tip = _held(env)
    monkeypatch.setattr(reflexd._owner_pin, "owner_pin", lambda _p: OWNER_PIN)
    relay = _Room([_owner_says(text.format(sha=tip[:12], other="c" * 12))])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main


def test_local_override_publishes_unreviewed(env):
    d, repo, wt, _main, tip = _held(env)
    rg.update_marker(d.config.runtime_dir, repo, wt.branch, override_tip=tip)
    relay = _Room([])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == tip
    assert relay.posted[0]["content"].endswith("; unreviewed (owner override by @b local)")


def test_local_override_for_an_older_tip_does_nothing(env):
    d, repo, wt, main, tip = _held(env)
    rg.update_marker(d.config.runtime_dir, repo, wt.branch, override_tip="f" * 40)
    _tick(d, _Room([]))
    assert _git(repo, "rev-parse", "main") == main


def test_override_gone_under_the_lock_holds(env, monkeypatch):
    d, repo, _wt, main, tip = _held(env)
    monkeypatch.setattr(reflexd._owner_pin, "owner_pin", lambda _p: OWNER_PIN)

    class Vanishing(_Room):
        reads = 0

        async def fetch_history(self, **_kw: Any) -> list[dict[str, Any]]:
            self.reads += 1
            return list(self.history) if self.reads <= 2 else []

    relay = Vanishing([_owner_says(f"publish {tip[:12]} unreviewed")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main


@pytest.mark.parametrize("after", [{"tenant": "t1", "claim_id": "c-new"}, None])
def test_owner_pin_rotated_or_removed_before_the_lock_holds(env, monkeypatch, after):
    # codex, review of a48abf0e: the lock-time check reused the captured pin.
    d, repo, _wt, main, tip = _held(env)
    pins = iter([OWNER_PIN, OWNER_PIN] + [after] * 10)  # rotated after the first read
    monkeypatch.setattr(reflexd._owner_pin, "owner_pin", lambda _p: next(pins))
    relay = _Room([_owner_says(f"publish {tip[:12]} unreviewed")])
    _tick(d, relay)
    assert _git(repo, "rev-parse", "main") == main
