"""Audit F2: a reviewer sees the commit itself, rendered by the daemon.

The demo's read-only reviewer approved the author's description because it had
no shell. The fix renders the commit in the daemon with git helpers off; the
reviewer's permissions stay read-only (codex, review of d2409830).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from quorus.runtime import commit_evidence as ce

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reflexd_models

_ID = ["-c", "user.name=t", "-c", "user.email=t@x", "-c", "commit.gpgsign=false"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *_ID, *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(r, "commit", "-qam", "fix: add() was subtracting")
    return r


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


# ── which words count as commit names ──────────────────────────────────────


def test_only_standalone_hex_words_are_candidates() -> None:
    text = ("committed as `2ca9cdf`, see HEAD, main, --output=x, refs/heads/main, "
            "abcdef (too short), b_abc1234567, path/1234567abc, 2ca9cdf again, "
            "DEADBEEF00 and 0123456789abcdef0123456789abcdef01234567.")
    assert ce.candidate_hashes(text) == [
        "2ca9cdf", "0123456789abcdef0123456789abcdef01234567"]


def test_candidates_are_bounded() -> None:
    text = " ".join(f"{i:07x}" for i in range(50))
    assert len(ce.candidate_hashes(text)) == ce.MAX_CANDIDATES


def test_resolve_takes_commits_only(repo: Path) -> None:
    head = _head(repo)
    blob = _git(repo, "rev-parse", "HEAD:calc.py")
    assert ce.resolve(repo, head[:7]) == head
    assert ce.resolve(repo, blob[:10]) is None  # a blob, not a commit
    assert ce.resolve(repo, "deadbee") is None  # nothing by that name
    for not_hex in ("HEAD", "main", "--output=x", "HEAD~1", "@{-1}"):
        assert ce.resolve(repo, not_hex) is None


# ── what the reviewer gets ─────────────────────────────────────────────────


def test_block_has_the_real_diff_fenced(repo: Path) -> None:
    head = _head(repo)
    block = ce.evidence_block(repo, f"Done, committed as `{head[:7]}`. @rev review",
                              "n0nce")
    assert f"## {head}" in block
    assert "+    return a + b" in block and "-    return a - b" in block
    assert block.index(ce.OPEN.format(nonce="n0nce")) < block.index("return a + b")
    assert block.index("return a + b") < block.index(ce.CLOSE.format(nonce="n0nce"))
    assert "author's summary is not evidence" in block


def test_no_resolvable_commit_means_no_block(repo: Path, tmp_path: Path) -> None:
    assert ce.evidence_block(repo, "looks good, ship it", "n") == ""
    assert ce.evidence_block(repo, "deadbee and cafebabe1", "n") == ""
    assert ce.evidence_block(None, _head(repo), "n") == ""
    assert ce.evidence_block(tmp_path / "missing", _head(repo), "n") == ""


def test_a_huge_commit_is_cut_with_a_note(repo: Path) -> None:
    (repo / "big.txt").write_text("x" * 80 + "\n" * 1 + ("line\n" * 20_000))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "big")
    block = ce.evidence_block(repo, _head(repo), "n")
    assert "[cut by Quorus at" in block
    assert len(block) < ce.TOTAL_CHARS + 2_000


def test_at_most_three_commits(repo: Path) -> None:
    shas = []
    for i in range(5):
        (repo / f"f{i}.txt").write_text(f"{i}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"c{i}")
        shas.append(_head(repo))
    block = ce.evidence_block(repo, " ".join(s[:8] for s in shas), "n")
    assert block.count("\n## ") == ce.MAX_COMMITS


# ── git must not run anything the repo asks it to ──────────────────────────


def _sentinel_script(tmp_path: Path, name: str, sentinels: Path) -> Path:
    script = tmp_path / f"{name}.sh"
    script.write_text(f"#!/bin/sh\necho {name} >> '{sentinels}'\ncat >/dev/null 2>&1\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_repo_config_and_env_cannot_run_programs(repo: Path, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    sentinels = tmp_path / "ran.txt"
    s = {n: _sentinel_script(tmp_path, n, sentinels) for n in (
        "external", "driver", "textconv", "pager", "fsmonitor", "gpg", "envdiff",
        "envpager", "envcfg")}
    for key, value in (
        ("diff.external", s["external"]),
        ("diff.evil.command", s["driver"]),
        ("diff.evil.textconv", s["textconv"]),
        ("core.pager", s["pager"]),
        ("pager.show", s["pager"]),
        ("core.fsmonitor", s["fsmonitor"]),
        ("log.showSignature", "true"),
        ("gpg.program", s["gpg"]),
    ):
        _git(repo, "config", key, str(value))
    (repo / ".gitattributes").write_text("* diff=evil\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "attributes")
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    _git(repo, "commit", "-qam", "swap")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(s["envdiff"]))
    monkeypatch.setenv("GIT_PAGER", str(s["envpager"]))
    monkeypatch.setenv("PAGER", str(s["envpager"]))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.pager")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(s["envcfg"]))

    sha = _head(repo)
    sentinels.unlink(missing_ok=True)  # the setup's own commits ran fsmonitor

    block = ce.evidence_block(repo, f"review {sha[:9]}", "n")

    assert not sentinels.exists(), sentinels.read_text()
    assert "+    return b + a" in block  # the real patch, not a driver's output


def test_a_missing_blob_never_lazy_fetches_through_a_repo_helper(repo: Path,
                                                                 tmp_path: Path) -> None:
    """A partial clone fetches missing blobs from its promisor remote on demand. A
    repo that names an ext:: remote, and allows ext in its own config, ran that
    helper the moment `git show` needed a blob (codex, review of 9e8249a6)."""
    sentinels = tmp_path / "ran.txt"
    helper = _sentinel_script(tmp_path, "lazyfetch", sentinels)
    _git(repo, "config", "uploadpack.allowFilter", "true")
    partial = tmp_path / "partial"
    subprocess.run(["git", "-c", "protocol.file.allow=always", "clone", "-q",
                    "--filter=blob:none", "--no-checkout", f"file://{repo}", str(partial)],
                   check=True, capture_output=True)
    _git(partial, "config", "remote.origin.url", f"ext::{helper}")
    _git(partial, "config", "protocol.ext.allow", "always")
    _git(partial, "config", "protocol.allow", "always")
    sha = _head(repo)

    block = ce.evidence_block(partial, f"review {sha[:9]}", "n")

    assert not sentinels.exists(), sentinels.read_text()
    assert "return a + b" not in block  # the blob isn't here, and nothing fetched it


def test_rendering_leaves_the_worktree_byte_identical(repo: Path) -> None:
    (repo / "wip.txt").write_text("uncommitted\n")
    sha = _head(repo)
    before = _git(repo, "status", "--porcelain=v2", "--ignored")
    index = repo / ".git" / "index"
    stamp = (index.stat().st_mtime_ns, index.read_bytes())
    ce.evidence_block(repo, sha, "n")
    # The index first: `git status` below may refresh it itself.
    assert (index.stat().st_mtime_ns, index.read_bytes()) == stamp
    assert _git(repo, "status", "--porcelain=v2", "--ignored") == before


# ── the reviewer stays read-only ───────────────────────────────────────────


def test_read_only_opencode_still_has_no_shell_or_writes() -> None:
    cfg = reflexd_models.opencode_config(None, mode="readonly")
    assert cfg["permission"] == {"edit": "deny", "bash": "deny", "webfetch": "deny"}
    denied = {r["action"] for r in cfg["permissions"] if r["effect"] == "deny"}
    assert {"edit", "write", "bash", "shell", "webfetch"} <= denied
    assert not [r for r in cfg["permissions"] if r["effect"] != "deny"]


def test_the_daemon_puts_evidence_in_the_wake_prompt() -> None:
    src = (Path(__file__).resolve().parents[1] / "scripts" / "reflexd.py").read_text()
    wake = src[src.index("Audit F2: a commit the message names"):]
    assert "_commit_evidence.evidence_block" in wake.split("_members =", 1)[0]
    assert os.path.exists(ce.__file__)
