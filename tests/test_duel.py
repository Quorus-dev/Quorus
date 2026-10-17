"""`quorus duel`: tests gate, a blind cross-family judge picks, the ledger learns."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from quorus_cli import duel_cmd

from quorus import ledger


def test_judge_comes_from_another_family() -> None:
    have = {"claude", "codex", "gemini", "opencode"}
    assert duel_cmd.choose_judge(["claude", "codex"], have) == "gemini"
    assert duel_cmd.choose_judge(["gemini", "opencode"], have) == "claude"
    assert duel_cmd.choose_judge(["claude", "claude"], have) == "codex"
    with pytest.raises(SystemExit):
        duel_cmd.choose_judge(["claude", "codex"], {"claude", "codex"})


def test_verdict_parsing() -> None:
    assert duel_cmd.parse_verdict('ok {"winner": "b", "reason": "smaller"}') == ("B", "smaller")
    assert duel_cmd.parse_verdict("Winner: A") == ("A", "")
    assert duel_cmd.parse_verdict("both are fine")[0] is None


def _repo(tmp: Path) -> Path:
    repo = tmp / "app"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n"
                                       "    assert add(2, 3) == 5\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


def _fake_reflexd(fixes: dict[str, str], verdict: str = "") -> Any:
    class Adapter:
        def __init__(self, timeout_s: int = 0) -> None:
            self.model: str | None = None

        async def run(self, harness: str, *, context: str, cwd: Path, mode: str = "",
                      **_k: Any) -> str:
            if mode == "readonly":  # the judge
                assert "Candidate A" in context and "Candidate B" in context
                assert "arav-" not in context  # blind: no names
                return verdict
            self.on_step("editing calc.py")  # the daemon's live steps, shown in the duel
            self.on_step("editing calc.py")
            (Path(cwd) / "calc.py").write_text(f"def add(a, b):\n    return {fixes[harness]}\n")
            return "done"
    return SimpleNamespace(HeadlessAdapter=Adapter)


def _args(repo: Path, **kw: Any) -> SimpleNamespace:
    base = dict(task="fix add()", agents="arav-claude,arav-codex", repo=str(repo), judge=None,
                judge_model=None,
                test=f"{sys.executable} -m pytest -q -p no:cacheprovider", timeout=60,
                no_merge=False, room="build")
    base.update(kw)
    return SimpleNamespace(**base)


class _C:
    def __init__(self) -> None:
        self.out: list[str] = []

    def print(self, *a: Any, **_k: Any) -> None:
        self.out.append(" ".join(map(str, a)))


def test_only_passing_entry_wins_without_a_judge(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(duel_cmd, "_load_reflexd",
                        lambda: _fake_reflexd({"claude": "a + b", "codex": "a * b"}))
    monkeypatch.setattr(duel_cmd.shutil, "which", lambda b: f"/bin/{b}")
    c = _C()
    duel_cmd.cmd_duel(_args(repo), c)
    assert "a + b" in (repo / "calc.py").read_text()  # winner merged into main
    assert any("Winner: @arav-claude" in line for line in c.out)
    shown = [line for line in c.out if "editing calc.py" in line]
    assert len(shown) == 2 and any("@arav-codex 0:00 editing calc.py" in s for s in shown)
    card = {r["agent"]: r for r in ledger.scorecard(ledger.outcomes(repo, "HEAD~1..HEAD"))}
    assert card["arav-claude"]["duels_won"] == 1 and card["arav-codex"]["duels_lost"] == 1
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "duel-" in branches and "arav-codex" in branches  # loser kept for reference


def test_both_pass_and_the_judge_decides(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(duel_cmd, "_load_reflexd", lambda: _fake_reflexd(
        {"claude": "a + b", "codex": "b + a"}, '{"winner": "A", "reason": "clearer"}'))
    monkeypatch.setattr(duel_cmd.shutil, "which", lambda b: f"/bin/{b}")
    monkeypatch.setattr(duel_cmd.random, "sample", lambda xs, k: list(xs))  # A = first agent
    c = _C()
    duel_cmd.cmd_duel(_args(repo), c)
    assert any("Winner: @arav-claude (claude). clearer" in line for line in c.out)


def test_refuses_a_dirty_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "calc.py").write_text("changed\n")
    with pytest.raises(SystemExit, match="uncommitted"):
        duel_cmd.cmd_duel(_args(repo), _C())


def test_agent_model_syntax(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    seen: dict[str, Any] = {}
    fake = _fake_reflexd({"claude": "a + b", "opencode": "a * b"})
    real_run = fake.HeadlessAdapter.run

    async def spy(self, harness, **kw):
        seen[harness] = self.model
        return await real_run(self, harness, **kw)

    fake.HeadlessAdapter.run = spy
    monkeypatch.setattr(duel_cmd, "_load_reflexd", lambda: fake)
    monkeypatch.setattr(duel_cmd.shutil, "which", lambda b: f"/bin/{b}")
    duel_cmd.cmd_duel(_args(repo, agents="arav-claude,arav-opencode=opencode/longcat"), _C())
    assert seen == {"claude": None, "opencode": "opencode/longcat"}
