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
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "revert", "--no-edit", bad)
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


def test_publish_lines_count_from_an_agent_worktree(tmp_path: Path) -> None:
    """Run from `quorus/<agent>` (which is ahead of or behind the room's branch),
    lines publishing into that branch still attribute; a diverged branch's don't.
    Before: only the checked-out branch counted, so codex and opencode vanished
    from the scorecard when it ran in an agent worktree (2026-10-17)."""
    from datetime import datetime, timezone

    repo, base = _repo(tmp_path)
    sha = _commit(repo, "fix: x", {"app.py": "x = 3\n"})
    _git(repo, "branch", "l2", base)
    _git(repo, "checkout", "-q", "l2")
    _commit(repo, "l2 only", {"l2.py": "1\n"})
    _git(repo, "checkout", "-qb", "quorus/a-claude", "main")
    _commit(repo, "agent wip", {"wip.py": "1\n"})  # ahead of main
    when = datetime.fromtimestamp(int(_git(repo, "log", "-1", "--format=%ct", sha)) + 2,
                                  timezone.utc).isoformat()
    line = "(quorus) published 1 commit(s) from quorus/{} to {}"
    history = [{"from_name": "a-opencode", "timestamp": when,
                "content": line.format("a-opencode", "main")},
               {"from_name": "l2-codex", "timestamp": when,
                "content": line.format("l2-codex", "l2")}]
    rows = {o.sha: o for o in ledger.outcomes(repo, f"{base}..main", history)}
    assert rows[sha].agent == "a-opencode" and rows[sha].inferred
    assert ledger._branches_in_line(repo, history) == {"quorus/a-claude", "main"}


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


def test_notes_need_no_git_identity(tmp_path: Path, monkeypatch) -> None:
    """Fresh machines and CI have no user.name: notes used to fail silently."""
    repo, base = _repo(tmp_path)
    _commit(repo, "feat: y", {"y.py": "y = 2\n"})
    cfg = tmp_path / "no-identity-gitconfig"
    cfg.write_text("[user]\n\tuseConfigOnly = true\n")  # like CI: no guessed identity
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(var, raising=False)
    assert ledger.record_provenance(repo, base, "HEAD", {"agent": "a-codex"}) == 1
    assert (ledger.read_provenance(repo, "HEAD") or {}).get("agent") == "a-codex"


def test_quoting_a_review_is_not_reviewing() -> None:
    """Gemini pasted retro evidence quoting Codex's block and was scored as a flagger."""
    sha = "6a947e63aaaa"
    history = [
        {"from_name": "a-codex", "timestamp": "2026-10-17T12:06:00Z",
         "content": "@a-claude /disagree blocking on `6a947e63`: legacy bypass"},
        {"from_name": "a-gemini", "timestamp": "2026-10-17T13:26:00Z",
         "content": "@a-claude top 3 from retro:\n   12:06 @a-codex: @a-claude /disagree "
                    "blocking on `6a947e63`\n> rejected 6a947e63 too"},
    ]
    assert ledger._review(history, sha, "a-claude") == ("flagged", ["a-codex"])


def test_a_mixed_review_judges_each_commit_by_its_own_lines() -> None:
    """Codex approved two commits and blocked something else in one message; the
    scorecard flagged all three (2026-10-17)."""
    history = [{"from_name": "a-codex", "timestamp": "2026-10-17T19:09:42Z", "content": (
        "@a-claude ✅ `44409868` and `830bc223` pass the reviewed opt-in path. `dc24ee6c` "
        "closes the startup race.\n/disagree blocking on the remaining daemon auth path.")}]
    assert ledger._review(history, "44409868ab", "a-claude") == ("approved", ["a-codex"])
    assert ledger._review(history, "830bc223ab", "a-claude") == ("approved", ["a-codex"])
    one = [{"from_name": "a-codex", "timestamp": "2026-10-17T12:00:00Z",
            "content": "Reviewed `6a947e63`.\n/disagree blocking: legacy bypass"}]
    assert ledger._review(one, "6a947e63ab", "a-claude") == ("flagged", ["a-codex"])
    shipped = [{"from_name": "b-codex", "timestamp": "2026-10-17T12:00:00Z",
                "content": "✅ 6a947e63 merged into weekend"}]  # a ship line, not a review
    assert ledger._review(shipped, "6a947e63ab", "a-claude") == ("none", [])


def test_scorecard_says_its_tests_column_counts_test_changes(tmp_path: Path) -> None:
    """0.6.3 audit BUG 14: a fix whose tests ran green showed "tests 0%" because it
    changed no test file; the table now says +tests and what that counts."""
    from types import SimpleNamespace

    from quorus_cli import scorecard_cmd
    from rich.console import Console

    repo, _ = _repo(tmp_path)
    _commit(repo, "fix: a", {"app.py": "x = 3\n"})
    console = Console(width=200, record=True, color_system=None)
    scorecard_cmd.cmd_scorecard(SimpleNamespace(repo=str(repo), range=None, since="1.year",
                                                room=None, json=False, by="agent"), console)
    text = console.export_text()
    assert "+tests" in text and "add or change a test (not whether tests ran)" in text


# ── survival: every file, one pinned end, renames followed (Codex, 2026-10-20) ──

def test_survival_counts_every_file_not_just_the_first_25(tmp_path: Path) -> None:
    """Codex's probe: 26 files with one intact line each reported 25 of 26 surviving."""
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "feat: wide", {f"f{i:02}.py": f"line {i}\n" for i in range(26)})
    [o] = ledger.outcomes(repo, f"{base}..HEAD")
    assert o.sha == sha and o.added == 26 and o.surviving == 26
    assert o.computed == 26 and o.survival == 1.0 and o.coverage == 1.0


def test_a_renamed_file_is_followed_and_a_deleted_one_counts_as_gone(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "feat: two files", {"keep.py": "a\nb\n", "drop.py": "c\nd\n"})
    _git(repo, "mv", "keep.py", "kept.py")
    _git(repo, "rm", "-q", "drop.py")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "move + delete")
    o = {r.sha: r for r in ledger.outcomes(repo, f"{base}..HEAD")}[sha]
    assert o.added == 4 and o.surviving == 2 and o.computed == 4  # renamed lines kept
    assert o.survival == 0.5 and o.files_uncomputed == 0


def test_an_unblameable_file_is_uncomputed_not_deleted(tmp_path: Path, monkeypatch) -> None:
    """Codex: renamed or uncomputed lines must never silently become deleted lines."""
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "feat: two", {"ok.py": "a\nb\n", "bad.py": "c\nd\n"})
    real = ledger.subprocess.run

    def flaky(argv, *a, **kw):
        if "blame" in argv and argv[-1] == "bad.py":
            return subprocess.CompletedProcess(argv, 128, "", "fatal: boom")
        return real(argv, *a, **kw)

    monkeypatch.setattr(ledger.subprocess, "run", flaky)
    o = {r.sha: r for r in ledger.outcomes(repo, f"{base}..HEAD")}[sha]
    assert o.added == 4 and o.computed == 2 and o.surviving == 2
    assert o.survival == 1.0 and o.coverage == 0.5 and o.files_uncomputed == 1
    card = ledger.scorecard([o])[0]
    assert card["survival"] == 1.0 and card["coverage"] == 0.5


def test_survival_is_measured_at_the_pinned_end_not_the_checkout(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "feat: x", {"x.py": "1\n2\n"})
    end = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "refactor: gone", {"x.py": "z\n"})
    at_end = {r.sha: r for r in ledger.outcomes(repo, f"{base}..{end}", end=end)}[sha]
    at_head = {r.sha: r for r in ledger.outcomes(repo, f"{base}..{end}")}[sha]
    assert at_end.survival == 1.0 and at_head.survival == 0.0


def test_a_rename_with_edits_is_parsed_as_a_path_not_old_arrow_new(tmp_path: Path) -> None:
    """Codex on f033a72d: numstat's `old.py => new.py` was read as one filename, so the
    rename commit's intact added line reported 0% survival at 100% coverage."""
    repo, base = _repo(tmp_path)
    _commit(repo, "feat: old", {"old.py": "".join(f"line {i}\n" for i in range(20))})
    _git(repo, "mv", "old.py", "new.py")
    (repo / "new.py").write_text("".join(f"line {i}\n" for i in range(20)) + "added\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "rename + edit")
    sha = _git(repo, "rev-parse", "HEAD")
    o = {r.sha: r for r in ledger.outcomes(repo, f"{base}..HEAD")}[sha]
    assert o.added == 1 and o.surviving == 1 and o.survival == 1.0 and o.coverage == 1.0


def test_unusual_file_names_are_measured(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "feat: odd", {'a b"cé.py': "x\ny\n"})
    o = {r.sha: r for r in ledger.outcomes(repo, f"{base}..HEAD")}[sha]
    assert o.added == 2 and o.surviving == 2 and o.survival == 1.0


def test_a_failed_tree_read_or_blame_timeout_is_uncomputed_not_deleted(
        tmp_path: Path, monkeypatch) -> None:
    """Codex on f033a72d: a fatal tree error counted two lines as deleted at 100% coverage,
    and a blame timeout escaped instead of becoming uncomputed."""
    repo, base = _repo(tmp_path)
    sha = _commit(repo, "feat: three", {"ok.py": "a\n", "tree.py": "b\nc\n", "slow.py": "d\n"})
    real = ledger.subprocess.run

    def broken(argv, *a, **kw):
        if "ls-tree" in argv and argv[-1] == "tree.py":
            return subprocess.CompletedProcess(argv, 128, "", "fatal: bad tree")
        if "blame" in argv and argv[-1] == "slow.py":
            raise subprocess.TimeoutExpired(argv, 60)
        return real(argv, *a, **kw)

    monkeypatch.setattr(ledger.subprocess, "run", broken)
    o = {r.sha: r for r in ledger.outcomes(repo, f"{base}..HEAD")}[sha]
    assert o.added == 4 and o.computed == 1 and o.surviving == 1
    assert o.files_uncomputed == 2 and o.coverage == 0.25 and o.survival == 1.0
