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
