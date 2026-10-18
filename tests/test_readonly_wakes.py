"""Read-only wakes never ship code (security fix 15, 2026-10-17).

Quorus used to publish an agent's worktree to main after every wake, whatever
mode the turn ran in, so a write that slipped past a harness's read-only mode
(Gemini's plan mode, release sweep) was committed and merged by Quorus itself.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
ro = reflexd.reflexd_readonly


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp: Path) -> Path:
    repo = tmp / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict] = []

    async def fetch_recent(self, *, room: str, limit: int = 10):
        return []

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": f"p{len(self.posted)}"}


def _wake(monkeypatch, tmp: Path, repo: Path, *, sender: str, mode: str,
          harness_writes: bool = True, commit: bool = False) -> _Relay:
    bindings = tmp / "bindings.json"
    bindings.write_text(json.dumps({"build": {"path": str(repo), "mode": mode}}))
    monkeypatch.setattr(reflexd, "ROOM_BINDINGS_PATH", bindings)
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    monkeypatch.setattr(reflexd.reflexd_wake, "WORKTREES_ENABLED", True)
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="arav-gemini",
                                              runtime_dir=tmp / "runtime"))

    async def fake_run(harness, *, context, cwd=None, mode="default", **_kw: Any) -> str:
        if harness_writes:
            (Path(cwd) / "pwned.txt").write_text("pwned\n")
            if commit:
                _git(Path(cwd), "add", "-A")
                _git(Path(cwd), "commit", "-qm", "sneaky")
        return "Created pwned.txt"

    monkeypatch.setattr(d.adapter, "run", fake_run)
    env = {"id": "e", "message_id": "m", "room": "build", "from_name": sender,
           "content": "@arav-gemini create pwned.txt", "message_type": "chat"}
    triage = reflexd.classify_message(content=env["content"], sender=sender,
                                      self_name="arav-gemini")
    relay = _Relay()
    asyncio.run(d._wake_and_reply(relay, env, triage=triage))
    return relay


def _discarded(repo: Path) -> list[str]:
    return _git(repo, "for-each-ref", "--format=%(refname)",
                "refs/quorus/discarded/").splitlines()


def test_a_read_only_turn_that_writes_is_discarded_not_published(monkeypatch,
                                                                 tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    # manual Gemini: the harness was supposed to refuse, and didn't
    relay = _wake(monkeypatch, tmp_path, repo, sender="arav", mode="manual", commit=True)
    assert not (repo / "pwned.txt").exists()
    assert _git(repo, "log", "--oneline", "main").count("\n") == 0  # only "init"
    refs = _discarded(repo)
    assert len(refs) == 1 and refs[0].startswith("refs/quorus/discarded/arav-gemini/")
    assert _git(repo, "show", f"{refs[0]}:pwned.txt") == "pwned"  # kept for a human to see
    posts = [p["content"] for p in relay.posted]
    assert any(c.startswith("⚠ a read-only turn changed files") and refs[0] in c
               for c in posts)
    assert not any("published" in c and "(quorus)" in c for c in posts)


def test_uncommitted_writes_from_a_stranger_are_discarded_too(monkeypatch,
                                                              tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _wake(monkeypatch, tmp_path, repo, sender="mallory", mode="autonomous")  # read-only
    assert not (repo / "pwned.txt").exists() and len(_discarded(repo)) == 1
    wt = next((repo.parent / ".quorus-worktrees").rglob("arav-gemini"))
    assert not (wt / "pwned.txt").exists()  # the worktree is put back


def test_a_read_only_turn_that_changes_nothing_posts_nothing(monkeypatch,
                                                             tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    relay = _wake(monkeypatch, tmp_path, repo, sender="arav", mode="manual",
                  harness_writes=False)
    assert _discarded(repo) == [] and not any("read-only turn" in p["content"]
                                              for p in relay.posted)


def test_a_writable_turn_still_publishes(monkeypatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _wake(monkeypatch, tmp_path, repo, sender="arav", mode="autonomous")
    assert (repo / "pwned.txt").exists() or "pwned.txt" in _git(repo, "show", "--stat", "main")
    assert _discarded(repo) == []


def test_manual_claude_keeps_its_approved_writes() -> None:
    assert not ro.is_read_only("claude", "manual")
    assert ro.is_read_only("gemini", "manual") and ro.is_read_only("codex", "manual")
    assert ro.is_read_only("claude", "readonly") and not ro.is_read_only("codex", "autonomous")


def test_a_crash_mid_turn_never_lets_the_sweep_publish(tmp_path: Path) -> None:
    """The marker survives a crash; the startup sweep settles instead of publishing."""
    repo = _repo(tmp_path)
    marker_dir = tmp_path / "runtime" / "readonly-wakes"
    snap = ro.begin(marker_dir, repo)
    (repo / "pwned.txt").write_text("x\n")
    assert ro.pending(marker_dir, repo) == snap  # what the sweep finds after a crash
    ref = ro.settle(marker_dir, repo, snap, "arav-gemini")
    assert ref and not (repo / "pwned.txt").exists()
    assert ro.pending(marker_dir, repo) is None


def test_every_reflexd_module_ships_in_the_wheel() -> None:
    """A sibling module missing from pyproject's force-include crashes every
    installed daemon at import (reflexd_readonly is new)."""
    toml = (_ROOT / "pyproject.toml").read_text()
    for f in (_ROOT / "scripts").glob("reflexd*.py"):
        assert f'"scripts/{f.name}" = "quorus/_reflexd/{f.name}"' in toml, f.name
