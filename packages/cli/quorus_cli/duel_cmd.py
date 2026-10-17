"""`quorus duel`: two vendors solve the same task, tests gate, a blind judge picks.

Weekend research (2026-10-17): nobody measures which vendor's work holds up
in YOUR repo. The outcome ledger measures it after the fact; a duel measures it
head to head:

1. Each contender gets a fresh worktree off the repo's current branch and the
   same task, and they run at the same time.
2. Each worktree's tests run. Only candidates whose tests pass are eligible.
3. When both pass, a judge from a DIFFERENT model family sees the two diffs as
   anonymous "A" and "B" (order randomised) and picks one, with a reason.
4. The winner is merged; the loser's branch is kept for reference. The
   provenance notes record the duel, so `quorus scorecard` learns from it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HARNESS_FAMILY = {"claude": "anthropic", "codex": "openai", "gemini": "google",
                  "opencode": "open", "cursor": "cursor", "cline": "cline"}
JUDGE_ORDER = ("claude", "codex", "gemini", "opencode")
MAX_DIFF = 30_000


@dataclass
class Entry:
    agent: str
    harness: str
    branch: str
    path: Path
    model: str | None = None
    reply: str = ""
    tests_passed: bool | None = None
    test_tail: str = ""
    diff: str = ""
    commits: int = 0
    seconds: float = 0.0


def harness_of(agent: str) -> str:
    for h in HARNESS_FAMILY:
        if agent.endswith(f"-{h}") or f"-{h}-" in agent or agent == h:
            return h
    raise SystemExit(f"duel: can't tell which tool {agent!r} uses (name it <you>-claude, "
                     "<you>-codex, <you>-gemini or <you>-opencode)")


def choose_judge(contenders: list[str], installed: set[str], forced: str | None = None) -> str:
    """A judge from a model family neither contender belongs to."""
    if forced:
        return forced
    taken = {HARNESS_FAMILY[c] for c in contenders}
    for h in JUDGE_ORDER:
        if h in installed and HARNESS_FAMILY[h] not in taken:
            return h
    raise SystemExit("duel: no judge from a different model family is installed "
                     "(install another of claude/codex/gemini/opencode, or pass --judge)")


def parse_verdict(text: str) -> tuple[str | None, str]:
    """`{"winner": "A", "reason": "..."}` anywhere in the judge's reply."""
    for m in re.finditer(r"\{[^{}]*\"winner\"[^{}]*\}", text or ""):
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        w = str(data.get("winner", "")).strip().upper()
        if w in ("A", "B"):
            return w, str(data.get("reason", ""))[:400]
    m = re.search(r"\bwinner\W+([AB])\b", text or "", re.IGNORECASE)
    return (m.group(1).upper(), "") if m else (None, "")


def eligible(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if e.tests_passed and e.commits > 0]


def task_prompt(task: str) -> str:
    return (f"{task}\n\nYou are one of two independent solutions to this task; work only in this "
            "repo. Make the change, add or update tests, run them, and commit with a "
            "conventional commit message. Your final message: what you changed and the "
            "test result.")


def judge_prompt(task: str, a: Entry, b: Entry) -> str:
    def side(label: str, e: Entry) -> str:
        return (f"## Candidate {label}\nTests: {'PASS' if e.tests_passed else 'FAIL'}\n"
                f"```diff\n{e.diff[:MAX_DIFF]}\n```\n")
    return ("You are judging two anonymous solutions to the same task. Do not run tools or "
            "edit files. Pick the better one for correctness, minimal scope, readability and "
            "test quality.\n\n"
            f"# Task\n{task}\n\n{side('A', a)}\n{side('B', b)}\n"
            'Reply with ONLY this JSON: {"winner": "A" or "B", "reason": "<one sentence>"}')


def _git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo,
                          capture_output=True, text=True, check=check, timeout=120)


def detect_tests(repo: Path) -> str | None:
    if (repo / "package.json").exists():
        return "npm test --silent"
    if any(repo.glob("test*")) or (repo / "tests").exists() or any(repo.glob("**/test_*.py")):
        return f"{sys.executable} -m pytest -q"
    return None


def _load_reflexd() -> Any:
    from quorus.runtime.supervisor import _reflexd_script_path

    path = _reflexd_script_path()
    spec = importlib.util.spec_from_file_location("quorus_duel_reflexd", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("quorus_duel_reflexd", mod)
    spec.loader.exec_module(mod)
    return mod


async def _run_entry(adapter: Any, e: Entry, task: str, common_git: Path, timeout: int,
                     console: Any = None) -> None:
    adapter.model = e.model  # each contender gets its own adapter and model
    start = time.time()
    last = [""]

    def show(step: str) -> None:  # live: "@a-codex 0:42 editing relay.py"
        if console is not None and step != last[0]:
            last[0] = step
            secs = int(time.time() - start)
            console.print(f"[dim]  @{e.agent} {secs // 60}:{secs % 60:02d} {step}[/]",
                          highlight=False)
    adapter.on_step = show
    e.reply = await adapter.run(e.harness, context=task_prompt(task), cwd=e.path,
                                timeout_s=timeout, mode="autonomous",
                                writable_roots=[e.path, common_git])
    e.seconds = time.time() - start


def _finish_entry(repo: Path, e: Entry, base: str, test_cmd: str | None) -> None:
    if _git(e.path, "status", "--porcelain").stdout.strip():  # left uncommitted: commit it
        _git(e.path, "add", "-A")
        _git(e.path, "-c", f"user.name={e.agent}",
             "-c", f"user.email={e.agent}@agents.quorus.local",
             "commit", "-qm", f"feat: duel entry by {e.agent}")
    e.commits = int(_git(repo, "rev-list", "--count", f"{base}..{e.branch}").stdout.strip() or 0)
    e.diff = _git(repo, "diff", f"{base}...{e.branch}").stdout
    if test_cmd:
        out = subprocess.run(test_cmd, shell=True, cwd=e.path, capture_output=True, text=True,
                             timeout=900)
        e.tests_passed = out.returncode == 0
        lines = ((out.stdout or "") + (out.stderr or "")).strip().splitlines()
        e.test_tail = lines[-1][:160] if lines else ""


def cmd_duel(args: Any, console: Any) -> None:
    repo = Path(args.repo or ".").expanduser().resolve()
    if _git(repo, "rev-parse", "--show-toplevel").stdout.strip() != str(repo):
        raise SystemExit(f"duel: run it at the top of a git repo (or pass --repo): {repo}")
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise SystemExit("duel: the repo has uncommitted changes; commit or stash them first")
    # "agent" or "agent=provider/model", e.g. arav-opencode=opencode/longcat-2.5-preview-free
    specs = [a.strip() for a in args.agents.split(",") if a.strip()]
    agents = [sp.split("=", 1)[0] for sp in specs]
    models = {sp.split("=", 1)[0]: sp.split("=", 1)[1] for sp in specs if "=" in sp}
    if len(agents) != 2:
        raise SystemExit("duel: give exactly two contenders, e.g. --agents arav-claude,arav-codex")
    harnesses = [harness_of(a) for a in agents]
    installed = {h for h in HARNESS_FAMILY if shutil.which("cursor-agent" if h == "cursor" else h)}
    for h in harnesses:
        if h not in installed:
            raise SystemExit(f"duel: `{h}` isn't installed")
    judge = choose_judge(harnesses, installed, args.judge)
    base_branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    common_git = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
                      .stdout.strip())
    duel_id = time.strftime("%m%d%H%M")
    root = repo.parent / ".quorus-worktrees" / repo.name
    entries = []
    for agent, h in zip(agents, harnesses):
        branch = f"quorus/duel-{duel_id}-{agent}"
        path = root / f"duel-{duel_id}-{agent}"
        _git(repo, "worktree", "add", "-q", "-b", branch, str(path), base, check=True)
        entries.append(Entry(agent, h, branch, path, models.get(agent)))
    test_cmd = args.test or detect_tests(repo)
    console.print(f"[bold]Duel[/] on {repo.name}@{base_branch}: "
                  f"@{agents[0]} vs @{agents[1]} · judge: {judge} · tests: {test_cmd or 'none'}")
    reflexd = _load_reflexd()

    async def both() -> None:
        await asyncio.gather(*(_run_entry(reflexd.HeadlessAdapter(timeout_s=args.timeout), e,
                                          args.task, common_git, args.timeout, console)
                               for e in entries))
    asyncio.run(both())
    for e in entries:
        _finish_entry(repo, e, base, test_cmd)
        mark = {True: "[success]tests pass[/]", False: "[error]tests fail[/]",
                None: "[dim]no tests[/]"}[e.tests_passed]
        console.print(f"  @{e.agent}: {e.commits} commit(s), {mark}, {e.seconds:.0f}s")

    ok = eligible(entries) if test_cmd else [e for e in entries if e.commits]
    winner, reason = None, ""
    if len(ok) == 1:
        winner, reason = ok[0], "the only entry whose tests passed"
    elif len(ok) == 2:
        a, b = random.sample(ok, 2)
        # Try judges from other families until one gives a clear verdict.
        judges = [judge] + [h for h in JUDGE_ORDER if h in installed and h != judge
                            and HARNESS_FAMILY[h] not in {HARNESS_FAMILY[x] for x in harnesses}]
        for j in judges:
            judge_adapter = reflexd.HeadlessAdapter(timeout_s=600)
            judge_adapter.model = getattr(args, "judge_model", None) if j == judge else None
            verdict = asyncio.run(judge_adapter.run(j, context=judge_prompt(args.task, a, b),
                                                    cwd=repo, timeout_s=600, mode="readonly"))
            pick, reason = parse_verdict(verdict)
            winner = {"A": a, "B": b}.get(pick or "")
            if winner is not None:
                judge = j
                break
            console.print(f"[warning]![/] judge {j} gave no clear verdict; trying the next")
    if winner is None:
        console.print("[warning]![/] No winner: nothing merged. Branches kept: "
                      + ", ".join(e.branch for e in entries))
        return
    loser = next(e for e in entries if e is not winner)
    console.print(f"\n[success]✓[/] Winner: @{winner.agent} ({winner.harness}). {reason}")
    if args.no_merge:
        console.print(f"[dim]not merging (--no-merge); winner branch: {winner.branch}[/]")
        return
    merged = _git(repo, "merge", "--ff-only", "-q", winner.branch)
    if merged.returncode != 0:
        raise SystemExit(f"duel: couldn't fast-forward {base_branch} to the winner: "
                         f"{merged.stderr.strip()[:200]}")
    from quorus.ledger import record_provenance

    record_provenance(repo, base, "HEAD", {
        "agent": winner.agent, "vendor": winner.harness, "room": args.room or "",
        "duel": {"id": duel_id, "result": "won", "against": loser.agent,
                 "against_vendor": loser.harness, "judge": judge, "reason": reason}})
    for e in entries:
        _git(repo, "worktree", "remove", "--force", str(e.path))
    console.print(f"[dim]merged into {base_branch}; @{loser.agent}'s entry kept on "
                  f"{loser.branch}[/]")
