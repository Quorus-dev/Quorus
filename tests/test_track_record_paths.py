"""Loop A, shadow only: per-path evidence beside each @open bid.

Agreed with codex (2026-10-18): log the agent's track record on the paths an
@open names, never apply it, and measure whether it predicted the turns that
actually ran. A shadow pick's alternative is never observed, so only predictive
accuracy on observed turns is reported; routing changes need a separate trial.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from quorus import ledger
from quorus.runtime import track_record as tr

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QUORUS_TRACK_BIDS", raising=False)
    monkeypatch.setenv("QUORUS_TUNING_FILE", str(tmp_path / "tuning.json"))
    tr._files_cache.clear()
    tr._ledger_cache.clear()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    return repo


def _agent_commit(repo: Path, agent: str, path: str, msg: str) -> str:
    old = _git(repo, "rev-parse", "HEAD")
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f.read_text() + msg + "\n" if f.exists() else msg + "\n")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", msg)
    new = _git(repo, "rev-parse", "HEAD")
    ledger.record_provenance(repo, old, new, {"agent": agent, "vendor": "claude"})
    return new


def test_path_scopes_reads_file_paths_not_urls_or_traversal():
    text = ("fix quorus/routes/rooms.py and website/src/main.ts; "
            "see https://example.com/a/b.html, ../etc/passwd.txt and v0.5.3")
    assert tr.path_scopes(text) == ["quorus/routes/", "website/src/"]


def test_path_counts_are_per_agent_and_per_path(tmp_path):
    repo = _repo(tmp_path)
    _agent_commit(repo, "a-claude", "quorus/routes/rooms.py", "one")
    bad = _agent_commit(repo, "a-claude", "quorus/routes/rooms.py", "two")
    _git(repo, "revert", "--no-edit", bad)
    _agent_commit(repo, "a-claude", "website/src/main.ts", "three")
    _agent_commit(repo, "b-codex", "quorus/routes/rooms.py", "four")
    assert tr.path_counts(repo, "a-claude", ["quorus/routes/"], NOW) == (1, 1)
    assert tr.path_counts(repo, "a-claude", ["website/src/"], NOW) == (1, 0)
    assert tr.path_counts(repo, "b-codex", ["quorus/routes/"], NOW) == (1, 0)
    assert tr.path_counts(repo, "a-claude", [], NOW) == (0, 0)


def test_path_evidence_is_logged_but_never_changes_the_bid(tmp_path):
    repo = _repo(tmp_path)
    for i in range(3):
        sha = _agent_commit(repo, "a-claude", "quorus/routes/rooms.py", f"c{i}")
        _git(repo, "revert", "--no-edit", sha)  # a poor record on that path
    text = "@open fix quorus/routes/rooms.py"
    with_paths = tr.decide_bid(0.6, "cap", message_id="m1", runtime_dir=tmp_path / "rt",
                               participant="a-claude", repo=repo, text=text)
    without = tr.decide_bid(0.6, "cap", message_id="m2", runtime_dir=tmp_path / "rt2",
                            participant="a-claude", repo=repo, text="")
    assert with_paths.bid == without.bid
    rows = tr.load_bids(tmp_path / "rt", "a-claude")
    assert rows[-1]["paths"] == ["quorus/routes/"]
    assert (rows[-1]["path_kept"], rows[-1]["path_reverted"]) == (0, 3)
    assert rows[-1]["path_factor"] < 1.0


def test_report_measures_whether_path_evidence_predicted_the_turn(tmp_path):
    rt = tmp_path / "rt"
    rows = [
        ("m1", {"paths": ["quorus/"], "path_kept": 0, "path_reverted": 4, "path_factor": 0.6},
         "errored"),
        ("m2", {"paths": ["quorus/"], "path_kept": 5, "path_reverted": 0, "path_factor": 1.2},
         "clean"),
        ("m3", {"paths": ["web/"], "path_kept": 1, "path_reverted": 0, "path_factor": 1.1},
         "clean"),
    ]
    for i, (mid, extra, outcome) in enumerate(rows):
        tr._append(rt, "a", "bids", {"ts": NOW + i, "message_id": mid, "arm": "shadow",
                                     "raw": 0.5, "would": 0.5, "multiplier": 1.0, "scope": "",
                                     **extra}, NOW + i)
        tr.record_turn(rt, "a", outcome, kind="open_todo", message_id=mid, now=NOW + i + 0.5)
    out = tr.report(rt, "a", NOW + 10)["path_predictive"]
    assert out["low"] == {"turns": 1, "failed": 1}
    assert out["high"] == {"turns": 1, "failed": 0}
    assert out["thin"] == 1  # m3: one commit there is too thin to count
