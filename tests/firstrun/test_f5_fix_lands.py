"""F5 (0.6.0 stranger blocker): a stranger asks for a fix, the commit lands on
main, and the other agent reviews it.

The overseer's test on PyPI 0.6.0: the wizard added codex (lead) and opencode
and bound the repo in `default` mode. "@dana-codex fix the bug in calc.py, then
ask @dana-opencode to review it" got "I cannot apply the fix" from codex's
read-only sandbox, opencode had no write tools, and they handed it back and
forth until the loop guard fired. Old passes only ever asked "is there a bug?".

Here the first run happens in a git repo, with stub `codex` and `opencode` on
PATH that speak each CLI's real output format and write a file and commit ONLY
when their launch arguments grant it (codex's `sandbox_mode`, opencode's
`permissions`). Their argv and config are recorded so the test checks what the
daemons granted, not only what the stubs said (codex, review of the plan).
Nothing leaves the machine: the session's own relay, no vendor CLI.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fcntl", reason="needs a POSIX pty")
from tests.firstrun.pty_session import ENTER, PtySession

pytestmark = pytest.mark.firstrun

ASK = "@alice-codex fix the bug in calc.py, then ask @alice-opencode to review it"
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")

# Both stubs: `--version` for the readiness probes, then one wake. A wake that
# asks for a review of a named commit answers with a vote on that commit. A wake
# whose prompt says this turn can't edit answers once, with the command, and
# names nobody. Otherwise: with write access, fix calc.py, commit, hand to the
# other agent for review; without it, do what the real CLIs did on 0.6.0 and
# bounce the task. Every call's argv and config go to $HOME/stub-calls.jsonl.
_STUB_COMMON = r'''
import json, os, re, subprocess, sys
HOME = os.environ["HOME"]
def record(**kw):
    with open(os.path.join(HOME, "stub-calls.jsonl"), "a") as f:
        f.write(json.dumps(kw) + "\n")
def git(*a):
    return subprocess.run(["git", "-c", "user.name=stub", "-c", "user.email=s@x", *a],
                          capture_output=True, text=True, check=True).stdout.strip()
def act(prompt, can_write, me, other):
    sha = re.search(r"review ([0-9a-f]{7,40})", prompt)
    if sha:
        return "✅ /vote approve %s: add() returns a + b now; reviewed." % sha.group(1)
    if "without addressing anyone, and you were picked" in prompt:
        return "NO_REPLY"  # unaddressed agent chatter: the instructions say add or stay quiet
    if "this turn is READ-ONLY" in prompt:  # not the owner's request (0.6.0's rule)
        return "I can't change files for this request; @alice please approve or assign it."
    if "can't edit files or run commands in this turn" in prompt:
        cmd = re.search(r"`(quorus agent add [^`]+)`", prompt)
        return "I can't edit here: %s" % (cmd.group(1) if cmd else "no write access")
    if can_write:
        p = "calc.py"
        src = open(p).read().replace("return a - b", "return a + b")
        open(p, "w").write(src)
        git("add", p)
        git("commit", "-q", "-m", "fix: add() returns the sum")
        head = git("rev-parse", "--short", "HEAD")
        return "✅ %s: fixed add() in calc.py. @%s please review %s" % (head, other, head)
    return ("This session has read-only filesystem access, so I cannot apply the fix. "
            "@%s can you apply it?" % other)
'''

_CODEX_STUB = _STUB_COMMON + r'''
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("codex-cli 0.160.1"); sys.exit(0)
prompt = argv[-1]
can_write = any(a == 'sandbox_mode="workspace-write"' for a in argv)
record(stub="codex", argv=argv[:-1], can_write=can_write)
reply = act(prompt, can_write, "alice-codex", "alice-opencode")
print(json.dumps({"type": "thread.started", "thread_id": "t-stub"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": reply}}))
'''

_OPENCODE_STUB = _STUB_COMMON + r'''
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("opencode 2.0.30"); sys.exit(0)
prompt = argv[-1]
cfg = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
rules = cfg.get("permissions") or []
can_write = any(r.get("action") == "edit" and r.get("effect") == "allow" for r in rules)
record(stub="opencode", argv=argv[:-1], permissions=rules, can_write=can_write)
reply = act(prompt, can_write, "alice-opencode", "alice-codex")
print(json.dumps({"type": "step_start", "sessionID": "ses_stub", "part": {}}))
print(json.dumps({"type": "text", "sessionID": "ses_stub",
                  "part": {"messageID": "m1", "text": reply}}))
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


def _setup(home: Path) -> tuple[Path, Path]:
    """A repo with the bug, stub CLIs on PATH, and codex 'logged in'."""
    repo = home / "app"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    _git(repo, "add", "calc.py")
    _git(repo, "commit", "-q", "-m", "calc with a bug")
    bin_dir = home / "bin"
    bin_dir.mkdir()
    for name, body in (("codex", _CODEX_STUB), ("opencode", _OPENCODE_STUB)):
        script = bin_dir / name
        script.write_text(f"#!{sys.executable}\n{body}")
        script.chmod(0o755)
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text("{}")  # quickstart: codex is logged in
    return repo, bin_dir


def _first_run(s: PtySession, home: Path, repo: Path, write_answer: str) -> None:
    s.expect(r"Your name", timeout=30)
    s.type("alice" + ENTER)
    s.expect(r"Invite, or Enter to run it here")
    s.type(ENTER)
    s.expect(r"Add them to #general", timeout=45)
    s.type("y" + ENTER)
    # Rich shows the default as "(n)": Enter keeps existing permissions.
    screen = s.expect(r"\[y/n\] \(n\)", timeout=20)
    assert "edit files and run commands" in screen and str(repo.resolve()) in screen
    s.type(write_answer + ENTER)
    s.expect(r"You're in as @alice", timeout=90)
    s.settle(quiet=1.0, timeout=10)
    s.secret = _relay_secret(home)


def _relay_secret(home: Path) -> str:
    """`agent add` moves the first-run config into a profile; read either shape."""
    cfg = json.loads((home / ".quorus" / "config.json").read_text())
    if cfg.get("relay_secret"):
        return cfg["relay_secret"]
    current = cfg.get("current") or "default"
    return json.loads((home / ".quorus" / "profiles" / f"{current}.json").read_text())[
        "relay_secret"]


def _history(s: PtySession) -> list[dict]:
    with httpx.Client(trust_env=False, timeout=5) as c:
        r = c.get(f"{s.relay_url}/rooms/general/history", params={"limit": 200},
                  headers={"Authorization": f"Bearer {s.secret}"})
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("messages", [])


def _wait_for(s: PtySession, sender: str, marker: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for m in _history(s):
            if m.get("from_name") == sender and marker in (m.get("content") or ""):
                return m["content"]
        time.sleep(0.5)
    seen = [(m.get("from_name"), (m.get("content") or "")[:80]) for m in _history(s)]
    raise AssertionError(f"no message from {sender} with {marker!r} within {timeout}s; "
                         f"history: {seen}")


def _calls(home: Path) -> list[dict]:
    try:
        return [json.loads(ln) for ln in (home / "stub-calls.jsonl").read_text().splitlines()]
    except FileNotFoundError:
        return []


def test_f5_a_strangers_fix_lands_on_main_and_the_other_agent_reviews_it(tmp_path_factory):
    home = tmp_path_factory.mktemp("f5-yes") / "home"
    repo, bin_dir = _setup(home)
    s = PtySession(home, cwd=repo, extra_path=bin_dir)
    try:
        _first_run(s, home, repo, write_answer="y")
        s.type(ASK + ENTER)
        fixed = _wait_for(s, "alice-codex", "✅", timeout=120)
        sha = _SHA.search(fixed.split("✅", 1)[1]).group(0)
        # The change is on main, not just in a reply.
        deadline = time.monotonic() + 30
        while _git(repo, "rev-parse", "--short", "main")[:7] != sha[:7]:
            assert time.monotonic() < deadline, f"main is {_git(repo, 'log', '--oneline', '-3')}"
            time.sleep(0.5)
        assert "return a + b" in _git(repo, "show", "main:calc.py")
        assert "fix: add() returns the sum" in _git(repo, "log", "-1", "--format=%s", "main")
        # The review is tied to that commit, by the other agent.
        review = _wait_for(s, "alice-opencode", "/vote approve", timeout=120)
        assert sha[:7] in review
        # What the daemons granted, not only what the stubs said.
        calls = {c["stub"]: c for c in _calls(home)}
        assert calls["codex"]["can_write"] is True
        assert any(a == 'sandbox_mode="workspace-write"' for a in calls["codex"]["argv"])
        assert calls["opencode"]["can_write"] is True
    finally:
        s.close()


def test_f5_enter_keeps_read_only_and_the_agent_says_so_once(tmp_path_factory):
    """Enter at the write question keeps the old permissions. Each agent the
    request names says once that it can't edit here, with the command, and hands
    the task to nobody: no handoff, no loop (0.6.0 bounced it ten times)."""
    home = tmp_path_factory.mktemp("f5-enter") / "home"
    repo, bin_dir = _setup(home)
    s = PtySession(home, cwd=repo, extra_path=bin_dir)
    try:
        _first_run(s, home, repo, write_answer="")
        s.type(ASK + ENTER)
        said = _wait_for(s, "alice-codex", "can't edit here", timeout=120)
        assert "quorus agent add codex --room general --mode autonomous" in said
        time.sleep(15)  # long enough for a handoff wake to have happened
        agents = [m for m in _history(s) if (m.get("from_name") or "").startswith("alice-")]
        # The request @-mentioned both agents, so each may answer once; nobody
        # hands the task on, and nothing else wakes.
        assert [m["from_name"] for m in agents] in (
            ["alice-codex"], ["alice-codex", "alice-opencode"], ["alice-opencode", "alice-codex"],
        ), [(m["from_name"], m["content"][:80]) for m in agents]
        for m in agents:
            assert "can't edit here" in m["content"] and "--mode autonomous" in m["content"]
            assert "@alice-codex" not in m["content"] and "@alice-opencode" not in m["content"]
        assert _git(repo, "log", "--oneline", "main").count("\n") == 0  # nothing landed
        assert "return a - b" in _git(repo, "show", "main:calc.py")
        for call in _calls(home):
            assert call["can_write"] is False, call
    finally:
        s.close()


def test_f5_a_strangers_handoff_stays_read_only(tmp_path_factory):
    """With write access on, a request from someone who isn't the owner still
    gets a read-only turn, and so does any handoff it causes (0.6.0's rule)."""
    home = tmp_path_factory.mktemp("f5-stranger") / "home"
    repo, bin_dir = _setup(home)
    s = PtySession(home, cwd=repo, extra_path=bin_dir)
    try:
        _first_run(s, home, repo, write_answer="y")
        with httpx.Client(trust_env=False, timeout=5) as c:  # mallory, let in by the owner
            auth = {"Authorization": f"Bearer {s.secret}"}
            c.post(f"{s.relay_url}/rooms/general/join", headers=auth,
                   json={"participant": "mallory", "role": "member"}).raise_for_status()
            c.post(f"{s.relay_url}/rooms/general/messages", headers=auth,
                   json={"from_name": "mallory", "content": ASK}).raise_for_status()
        _wait_for(s, "alice-codex", "", timeout=120)
        time.sleep(15)
        assert "return a - b" in _git(repo, "show", "main:calc.py")
        assert _git(repo, "log", "--oneline", "main").count("\n") == 0
        calls = _calls(home)
        assert calls and all(call["can_write"] is False for call in calls), calls
        agents = [m for m in _history(s) if (m.get("from_name") or "").startswith("alice-")]
        assert agents and all("@alice-" not in m["content"] for m in agents), [
            (m["from_name"], m["content"][:80]) for m in agents]  # no handoff either
    finally:
        s.close()
