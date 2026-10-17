"""The daemon's own git never runs repo hooks (pre-launch audit, 2026-10-16).

A sandboxed agent can edit tracked hooks (husky-style ``core.hooksPath``)
in its worktree; the daemon commits/rebases/merges for it unsandboxed, so
running hooks there executed agent code outside the sandbox, and a
post-merge hook fired in the human's main checkout on publish.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if "reflexd_wake" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "reflexd_wake", _REPO_ROOT / "scripts" / "reflexd_wake.py")
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["reflexd_wake"] = _mod
    _spec.loader.exec_module(_mod)
w = sys.modules["reflexd_wake"]

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _g(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _hook(path: Path, marker: Path) -> None:
    path.write_text(f"#!/bin/sh\necho ran > '{marker}'\nexit 0\n")
    path.chmod(0o755)


def test_agent_edited_hooks_never_run_in_daemon_git(tmp_path: Path) -> None:
    repo, marker = tmp_path / "proj", tmp_path / "PWNED"
    repo.mkdir()
    _g("init", "-q", "-b", "main", cwd=repo)
    _g("config", "user.email", "h@example.com", cwd=repo)
    _g("config", "user.name", "h", cwd=repo)
    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    (repo / "a.txt").write_text("a\n")
    _g("add", "-A", cwd=repo)
    _g("commit", "-qm", "init", cwd=repo)
    _g("config", "core.hooksPath", ".githooks", cwd=repo)

    wt = w.agent_worktree(repo, "bob-codex")
    assert wt is not None
    # What a prompt-injected, sandboxed agent can do inside its worktree:
    for name in ("pre-commit", "post-commit", "post-merge", "post-rewrite"):
        _hook(wt.path / ".githooks" / name, marker)
    (wt.path / "a.txt").write_text("changed\n")

    assert w.commit_all(wt, "bob-codex", "work")
    assert not marker.exists(), "daemon commit ran an agent-edited hook"
    w.publish_worktree(wt, "bob-codex")
    assert not marker.exists(), "publish ran an agent-edited hook in the main checkout"
    assert (repo / "a.txt").read_text() == "changed\n"  # publishing still works
