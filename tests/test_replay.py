"""`quorus replay` (2026-10-17): a local, self-contained page of a room's work.
It must escape everything, mask credentials and never need the network."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from quorus_cli import replay_cmd

SECRET = "sk-test-0123456789abcdefghij"


def _msgs() -> list[dict[str, Any]]:
    return [
        {"from_name": "arav", "timestamp": "2026-10-17T12:00:00Z",
         "content": "fix add() please <script>alert(1)</script>"},
        {"from_name": "arav-claude", "timestamp": "2026-10-17T12:00:20Z",
         "content": f"plan: fixing it, token {SECRET}"},
        {"from_name": "arav-codex", "timestamp": "2026-10-17T12:01:00Z",
         "message_type": "status", "content": "read it, nothing to add"},
    ]


def test_page_escapes_html_masks_secrets_and_counts_the_work() -> None:
    commit = replay_cmd.Commit("abc12345", 1792238470.0, "fix: add() adds", "arav-claude",
                               "claude", "1 file changed, 1 insertion(+)")
    page = replay_cmd.render("build", _msgs(), [commit], known=[SECRET])
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert SECRET not in page
    assert "commit abc12345" in page and "@arav-claude · claude" in page
    assert '<li class="msg quiet">' in page and '<li class="msg human">' in page
    assert "<b>2</b><span>agents</span>" in page and "<b>1</b><span>commits</span>" in page
    assert "<script" not in page.replace("&lt;script", "")  # no scripts: works offline
    assert "http" not in page.split("<footer>")[0].replace("http-equiv", "")  # no fetches


def test_commits_in_the_window_carry_their_agent(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "arav-codex", "GIT_AUTHOR_EMAIL": "c@x",
           "GIT_COMMITTER_NAME": "c", "GIT_COMMITTER_EMAIL": "c@x", "PATH": "/usr/bin:/bin"}
    for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "feat: x"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
    import time

    rows = replay_cmd.commits_in(repo, time.time() - 600, time.time())
    assert [(c.subject, c.author, c.vendor) for c in rows] == [("feat: x", "arav-codex", "codex")]
    assert replay_cmd.commits_in(repo, time.time() + 3600, time.time() + 7200) == []


def test_cmd_writes_the_file_locally(tmp_path: Path, monkeypatch) -> None:
    import quorus_cli.retro_cmd as retro_cmd
    import quorus_cli.scorecard_cmd as scorecard_cmd

    monkeypatch.setattr(scorecard_cmd, "_history", lambda room: _msgs())
    monkeypatch.setattr(retro_cmd, "_since", lambda h, hours: h)
    monkeypatch.setattr(replay_cmd, "load_config", lambda: {"relay_secret": SECRET})
    monkeypatch.setattr(replay_cmd, "bound_repo", lambda room: None)
    out = tmp_path / "r.html"

    class C:
        def print(self, *a: Any, **k: Any) -> None:
            pass

    replay_cmd.cmd_replay(SimpleNamespace(room="build", since=2.0, repo=None, out=str(out)), C())
    text = out.read_text()
    assert "#build" in text and SECRET not in text
