"""Outcome ledger + `quorus scorecard` (2026-10-17).

Which agent and vendor's work survives in this repo: provenance notes at
publish, then tests / review / revert / survival per commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from quorus import ledger


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


def _commit(repo: Path, msg: str, files: dict[str, str]) -> str:
    for name, body in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, "init", {"app.py": "x = 1\n"})
    return repo, base


def test_provenance_note_round_trips_without_changing_the_commit(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "fix: a", {"app.py": "x = 2\n", "tests/test_app.py": "ok\n"})
    n = ledger.record_provenance(repo, base, "main", {"agent": "a-codex", "vendor": "codex",
                                                      "model": None, "room": "build"})
    assert n == 1 and _git(repo, "rev-parse", "HEAD") == sha  # commit id unchanged
    prov = ledger.read_provenance(repo, sha)
    assert prov and prov["agent"] == "a-codex" and prov["room"] == "build"


def test_outcomes_measure_tests_survival_and_reverts(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    kept = _commit(repo, "feat: kept", {"lib.py": "a\nb\nc\nd\n", "tests/test_lib.py": "t\n"})
    ledger.record_provenance(repo, base, "main", {"agent": "a-codex", "vendor": "codex"})
    mark = _git(repo, "rev-parse", "HEAD")
    churned = _commit(repo, "feat: churned", {"other.py": "1\n2\n3\n4\n"})
    ledger.record_provenance(repo, mark, "main", {"agent": "a-gemini", "vendor": "gemini"})
    _commit(repo, "refactor: rewrite", {"other.py": "1\nz\nz\nz\n"})  # 3 of 4 lines gone
    bad = _commit(repo, "fix: bad", {"bad.py": "oops\n"})
    _git(repo, "revert", "--no-edit", bad)
    rows = {o.sha: o for o in ledger.outcomes(repo, f"{base}..HEAD")}
    assert rows[kept].has_tests and rows[kept].survival == 1.0 and rows[kept].agent == "a-codex"
    assert rows[churned].survival == 0.25 and rows[churned].vendor == "gemini"
    assert rows[bad].reverted
    card = {r["agent"]: r for r in ledger.scorecard(list(rows.values()))}
    assert card["a-codex"]["with_tests"] == 1 and card["a-gemini"]["survival"] == 0.25


def test_room_reviews_and_publish_lines(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "fix: x", {"app.py": "x = 3\n"})
    ts = int(_git(repo, "log", "-1", "--format=%ct"))
    from datetime import datetime, timezone
    when = datetime.fromtimestamp(ts + 2, timezone.utc).isoformat()
    history = [
        {"from_name": "a-opencode", "timestamp": when,
         "content": "(quorus) published 1 commit(s) from quorus/a-opencode to main"},
        {"from_name": "laptop2-opencode", "timestamp": when,  # another branch: ignored
         "content": "(quorus) published 1 commit(s) from quorus/laptop2-opencode to weekend/l2"},
        {"from_name": "a-claude", "timestamp": when,
         "content": f"Reviewed {sha[:7]}: found a regression"},
        {"from_name": "a-claude", "timestamp": when + "Z"[:0],
         "content": f"{sha[:7]} the regression was fixed, approved"},
    ]
    (row,) = ledger.outcomes(repo, f"{base}..HEAD", history)
    assert row.agent == "a-opencode" and row.inferred
    assert row.review == "approved" and row.reviewers == ["a-claude"]


def test_a_flag_without_resolution_counts_as_flagged(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "fix: y", {"app.py": "x = 4\n"})
    history = [{"from_name": "a-codex", "timestamp": "2026-10-17T00:00:00Z",
                "content": f"/disagree blocking on {sha[:7]}: tests don't cover it"}]
    (row,) = ledger.outcomes(repo, f"{base}..HEAD", history)
    assert row.review == "flagged"


def test_daemon_publish_attaches_provenance(tmp_path: Path) -> None:
    import importlib.util
    import sys

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("reflexd_wake",
                                                  root / "scripts" / "reflexd_wake.py")
    assert spec and spec.loader
    wake = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("reflexd_wake", wake)
    spec.loader.exec_module(wake)
    repo, _base = _repo(tmp_path)
    wt = wake.agent_worktree(repo, "a-codex")
    assert wt is not None
    (wt.path / "feature.py").write_text("y = 1\n")
    _git(wt.path, "add", "-A")
    _git(wt.path, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "feat: y")
    status = wake.publish_worktree(wt, "a-codex", {"agent": "a-codex", "vendor": "codex",
                                                   "model": "gpt-6", "room": "build"})
    assert status and "published 1 commit" in status
    prov = ledger.read_provenance(repo, _git(repo, "rev-parse", "main"))
    assert prov and prov["vendor"] == "codex" and prov["model"] == "gpt-6"
