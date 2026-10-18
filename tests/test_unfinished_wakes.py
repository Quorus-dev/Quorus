"""A turn that runs out of time never publishes (0.5.1, overseer's triage).

Gemini's cut-off turn published a half-written CHANGELOG that deleted 86 lines
(c36101ec). Its commits now go to quorus/<agent>-unfinished for review, and
the room's branch stays put until a turn finishes cleanly.
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
TIMED_OUT = "[reflexd] harness timed out"
UNFINISHED = "quorus/arav-gemini-unfinished"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp: Path) -> Path:
    repo = tmp / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "CHANGELOG.md").write_text("".join(f"line {i}\n" for i in range(90)))
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


def _daemon(monkeypatch, tmp: Path, repo: Path):
    bindings = tmp / "bindings.json"
    bindings.write_text(json.dumps({"build": {"path": str(repo), "mode": "autonomous"}}))
    monkeypatch.setattr(reflexd, "ROOM_BINDINGS_PATH", bindings)
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    monkeypatch.setattr(reflexd.reflexd_wake, "WORKTREES_ENABLED", True)
    return reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                                 participant_name="arav-gemini",
                                                 runtime_dir=tmp / "runtime"))


def _wake(monkeypatch, d, *, reply: str, edit: str | None = None,
          commit: bool = True, msg: str = "m") -> _Relay:
    async def fake_run(harness, *, context, cwd=None, mode="default", **_kw: Any) -> str:
        if edit is not None:
            (Path(cwd) / "CHANGELOG.md").write_text(edit)
            if commit:
                _git(Path(cwd), "commit", "-qam", "docs: changelog")
        return reply

    monkeypatch.setattr(d.adapter, "run", fake_run)
    env = {"id": f"e-{msg}", "message_id": msg, "room": "build", "from_name": "arav",
           "content": "@arav-gemini update the changelog", "message_type": "chat"}
    triage = reflexd.classify_message(content=env["content"], sender="arav",
                                      self_name="arav-gemini")
    relay = _Relay()
    asyncio.run(d._wake_and_reply(relay, env, triage=triage))
    return relay


def _branches(repo: Path) -> list[str]:
    return _git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_a_cut_off_turn_parks_its_commits_instead_of_publishing(monkeypatch,
                                                                tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    relay = _wake(monkeypatch, d, reply=TIMED_OUT, edit="line 0\n")  # truncated
    assert _git(repo, "log", "--oneline", "main").count("\n") == 0  # main: only init
    assert (repo / "CHANGELOG.md").read_text().count("\n") == 90
    assert _git(repo, "show", f"{UNFINISHED}:CHANGELOG.md") == "line 0"
    posts = [p["content"] for p in relay.posted]
    assert any(UNFINISHED in c and "review and merge, or mention me to finish" in c
               for c in posts), posts
    assert not any("published" in c and "(quorus)" in c for c in posts)


def test_uncommitted_work_from_a_cut_off_turn_is_parked_too(monkeypatch,
                                                            tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    _wake(monkeypatch, d, reply=TIMED_OUT, edit="half\n", commit=False)
    assert _git(repo, "show", f"{UNFINISHED}:CHANGELOG.md") == "half"
    assert (repo / "CHANGELOG.md").read_text().count("\n") == 90


def test_the_sweep_never_publishes_a_parked_turn(monkeypatch, tmp_path: Path) -> None:
    """A restart runs the publish sweep; the marker keeps the work parked."""
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    _wake(monkeypatch, d, reply=TIMED_OUT, edit="line 0\n")
    asyncio.run(d._publish_sweep(_Relay()))
    assert _git(repo, "log", "--oneline", "main").count("\n") == 0


def test_the_finishing_turn_publishes_and_drops_the_review_copy(monkeypatch,
                                                                tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    _wake(monkeypatch, d, reply=TIMED_OUT, edit="line 0\n")
    finished = "".join(f"line {i}\n" for i in range(91))
    relay = _wake(monkeypatch, d, reply="✅ changelog done", edit=finished, msg="m2")
    assert any("published" in p["content"] for p in relay.posted)
    assert (repo / "CHANGELOG.md").read_text() == finished  # published to main
    assert UNFINISHED not in _branches(repo)
    assert not any((tmp_path / "runtime" / "unfinished-wakes").glob("*.json"))


def test_a_cut_off_turn_that_changed_nothing_says_so_as_before(monkeypatch,
                                                               tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    relay = _wake(monkeypatch, d, reply=TIMED_OUT)
    assert UNFINISHED not in _branches(repo)
    assert any(c["content"].startswith("⚠ ran out of time") and "Mention me" in c["content"]
               for c in relay.posted)


def test_a_failed_park_still_holds_the_work_back(monkeypatch, tmp_path: Path) -> None:
    """Codex on 87d7c3a5: the hold is written before parking, so a parking
    failure can't leave the cut-off commit for the startup sweep to publish."""
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)

    def fail_park(*_a: Any, **_k: Any) -> None:
        raise OSError("simulated branch failure")

    monkeypatch.setattr(reflexd.reflexd_unfinished, "park", fail_park)
    _wake(monkeypatch, d, reply=TIMED_OUT, edit="half\n")
    asyncio.run(d._publish_sweep(_Relay()))
    assert (repo / "CHANGELOG.md").read_text().count("\n") == 90


def test_even_an_unwritable_hold_blocks_this_process(monkeypatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)

    def fail(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(reflexd.reflexd_unfinished, "hold", fail)
    _wake(monkeypatch, d, reply=TIMED_OUT, edit="half\n")
    asyncio.run(d._publish_sweep(_Relay()))
    assert (repo / "CHANGELOG.md").read_text().count("\n") == 90


def test_a_failed_retry_keeps_the_hold(monkeypatch, tmp_path: Path) -> None:
    """Codex on 87d7c3a5: a retry that errors is not a finish."""
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    _wake(monkeypatch, d, reply=TIMED_OUT, edit="half\n")
    _wake(monkeypatch, d, reply="[reflexd] harness errored", msg="retry")
    asyncio.run(d._publish_sweep(_Relay()))
    assert (repo / "CHANGELOG.md").read_text().count("\n") == 90
    assert UNFINISHED in _branches(repo)


def test_an_errored_turn_with_new_commits_is_parked_too(monkeypatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    relay = _wake(monkeypatch, d, reply="[reflexd] harness errored", edit="half\n")
    assert (repo / "CHANGELOG.md").read_text().count("\n") == 90
    assert _git(repo, "show", f"{UNFINISHED}:CHANGELOG.md") == "half"
    assert any(UNFINISHED in p["content"] for p in relay.posted)
