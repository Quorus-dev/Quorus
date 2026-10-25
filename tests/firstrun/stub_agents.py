"""Stub agent CLIs and relay helpers for first-run tests (F5, bet 1's harness).

The stubs speak each CLI's real output format and write a file and commit ONLY
when their launch arguments grant it (codex's `sandbox_mode`, opencode's
`permissions`). Every call's argv and config go to $HOME/stub-calls.jsonl, so a
test checks what the daemons granted, not only what the stubs said (codex,
review of the F5 plan). Nothing leaves the machine.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

OWNER = "alice"

# Every stub: `--version` for the readiness probes, then one reply per wake. A
# wake that asks for a review of a named commit answers with a vote on that
# commit. A wake whose prompt says this turn can't edit answers once, with the
# command, and names nobody. A wake that isn't the calc.py fix says hello.
# Otherwise: with write access, fix calc.py, commit, and hand to the other agent
# for review if there is one; without it, do what the real CLIs did on 0.6.0
# and bounce the task.
_STUB_COMMON = r'''
import json, os, re, subprocess, sys
HOME = os.environ["HOME"]
OTHER = %(other)r
PICKED = "without addressing anyone, and you were picked"  # unaddressed chatter
def record(**kw):
    with open(os.path.join(HOME, "stub-calls.jsonl"), "a") as f:
        f.write(json.dumps(kw) + "\n")
def git(*a):
    return subprocess.run(["git", "-c", "user.name=stub", "-c", "user.email=s@x", *a],
                          capture_output=True, text=True, check=True).stdout.strip()
def from_agent(prompt):
    return "Your teammate agent `" in prompt and "@-mentioned you" in prompt
def act(prompt, can_write):
    # What the wake asks: only the triggering message, not the transcript above it,
    # where an earlier request or review would answer a later wake.
    asked = prompt.rsplit("# Triggering message", 1)[-1]
    sha = re.search(r"review ([0-9a-f]{7,40})", asked)
    if sha:
        # The review gate counts a GO that names the commit (quorus/runtime/review_gate.py);
        # the vote line stays for the ledger and the case study.
        return ("✅ GO on %%s: add() returns a + b now; reviewed. /vote approve %%s"
                %% (sha.group(1), sha.group(1)))
    if PICKED in prompt:
        return "NO_REPLY"  # unaddressed agent chatter: the instructions say add or stay quiet
    if "this turn is READ-ONLY" in prompt:  # not the owner's request (0.6.0's rule)
        if from_agent(prompt):  # the handoff landed here: stop, don't bounce it back
            return "I'm read-only for this request too; @%(owner)s please approve or assign it."
        # What the real CLIs did on 0.6.0: hand the stranger's task to the other agent.
        return ("This request isn't from my owner, so this turn is read-only. "
                "@%%s can you apply it?" %% OTHER)
    if "can't edit files or run commands in this turn" in prompt:
        cmd = re.search(r"`(quorus agent add [^`]+)`", prompt)
        return "I can't edit here: %%s" %% (cmd.group(1) if cmd else "no write access")
    if "fix the bug in calc.py" not in asked:
        return "Hi: I work in this repo. Ask me to fix or build something."
    me = re.search(r"You are `([^`]+)`", prompt)
    if me and re.search(r"ask @%%s to review" %% re.escape(me.group(1)), asked):
        # Named as the reviewer in the human's ask, which wakes this agent too: wait
        # for the fix. Fixing it as well raced the other agent's publish (PR #15 F5).
        return "I'll review it once the fix is posted."
    if can_write:
        p = "calc.py"
        src = open(p).read().replace("return a - b", "return a + b")
        open(p, "w").write(src)
        git("add", p)
        git("commit", "-q", "-m", "fix: add() returns the sum")
        head = git("rev-parse", "--short", "HEAD")
        if OTHER is None:
            return "✅ %%s: fixed add() in calc.py; test_calc.py passes." %% head
        return "✅ %%s: fixed add() in calc.py. @%%s please review %%s" %% (head, OTHER, head)
    return ("This session has read-only filesystem access, so I cannot apply the fix. "
            "@%%s can you apply it?" %% OTHER)
'''

_CODEX_STUB = r'''
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("codex-cli 0.160.1"); sys.exit(0)
if argv[:1] != ["exec"]:  # some other probe: not a wake, nothing to record
    sys.exit(0)
prompt = argv[-1]
can_write = any(a == 'sandbox_mode="workspace-write"' for a in argv)
record(stub="codex", argv=argv[:-1], can_write=can_write, from_agent=from_agent(prompt),
       untrusted="this turn is READ-ONLY" in prompt, picked=PICKED in prompt,
       trigger=prompt.rsplit("# Triggering message", 1)[-1][:600])
reply = act(prompt, can_write)
print(json.dumps({"type": "thread.started", "thread_id": "t-stub"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": reply}}))
'''

_OPENCODE_STUB = r'''
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("opencode 2.0.30"); sys.exit(0)
if argv[:1] != ["run"]:  # e.g. `opencode models`, the free-model catalog: not a wake
    sys.exit(0)
prompt = argv[-1]
cfg = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
rules = cfg.get("permissions") or []
can_write = any(r.get("action") == "edit" and r.get("effect") == "allow" for r in rules)
record(stub="opencode", argv=argv[:-1], permissions=rules, can_write=can_write,
       from_agent=from_agent(prompt), untrusted="this turn is READ-ONLY" in prompt,
       picked=PICKED in prompt)
reply = act(prompt, can_write)
print(json.dumps({"type": "step_start", "sessionID": "ses_stub", "part": {}}))
print(json.dumps({"type": "text", "sessionID": "ses_stub",
                  "part": {"messageID": "m1", "text": reply}}))
'''

_STUBS = {"codex": _CODEX_STUB, "opencode": _OPENCODE_STUB}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


def _patch_ids(repo: Path, *rev: str) -> dict[str, str]:
    """{commit: stable patch-id} for `git log -p <rev>`."""
    log = subprocess.run(["git", "-C", str(repo), "log", "-p", "--no-color", *rev],
                         capture_output=True, check=True).stdout
    ids = subprocess.run(["git", "-C", str(repo), "patch-id", "--stable"], input=log,
                         capture_output=True, check=True).stdout.decode().split("\n")
    return {c: p for p, c in (ln.split() for ln in ids if ln.strip())}


def landed(repo: Path, sha: str, branch: str = "main") -> str | None:
    """The commit on *branch* that carries *sha*'s change, or None.

    The ✅ names the agent's own commit. What reaches main can be a different one with
    the same change: rebased onto a main that moved, cherry-picked back after a hold
    (reflexd_held), or redone by a later wake. Matching on the patch-id, not the SHA,
    keeps F5 about "the fix landed" rather than "this exact object landed"."""
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, branch],
                      capture_output=True).returncode == 0:
        return git(repo, "rev-parse", sha)
    try:
        want = list(_patch_ids(repo, "-1", sha).values())
        on_branch = _patch_ids(repo, "-50", branch)
    except subprocess.CalledProcessError:
        return None
    return next((c for c, p in on_branch.items() if want and p == want[0]), None)


def setup(home: Path, tools: tuple[str, ...] = ("codex", "opencode"),
          owner: str = OWNER, reviewer: str | None = None) -> tuple[Path, Path]:
    """A repo with the bug and its own failing test, stub CLIs for *tools* (whose
    agents are *owner*'s) on PATH, and codex 'logged in' if it's one of them. A fix
    is handed to *reviewer* (default: the owner's other agent, if any)."""
    repo = home / "app"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    git(repo, "add", "calc.py", "test_calc.py")
    git(repo, "commit", "-q", "-m", "calc with a bug")
    bin_dir = home / "bin"
    bin_dir.mkdir()
    for tool in tools:
        others = [t for t in tools if t != tool]
        other = reviewer or (f"{owner}-{others[0]}" if others else None)
        script = bin_dir / tool
        common = _STUB_COMMON % {"other": other, "owner": owner}
        script.write_text(f"#!{sys.executable}\n{common}{_STUBS[tool]}")
        script.chmod(0o755)
    if "codex" in tools:
        (home / ".codex").mkdir()
        (home / ".codex" / "auth.json").write_text("{}")  # quickstart: codex is logged in
    return repo, bin_dir


def relay_secret(home: Path) -> str:
    """`agent add` moves the first-run config into a profile; read either shape."""
    cfg = json.loads((home / ".quorus" / "config.json").read_text())
    if cfg.get("relay_secret"):
        return cfg["relay_secret"]
    current = cfg.get("current") or "default"
    return json.loads((home / ".quorus" / "profiles" / f"{current}.json").read_text())[
        "relay_secret"]


POLL_S = 1.5  # the test relay rate-limits: a 0.5 s poll under a loaded suite got 429s


class RelayBusy(Exception):
    """The test relay answered 429; *wait* is its Retry-After, capped."""

    def __init__(self, wait: float) -> None:
        super().__init__(f"relay busy, retry in {wait:g}s")
        self.wait = wait


def retry_after(value: str | None, default: float = 2.0, cap: float = 10.0) -> float:
    try:
        return min(max(float(value), 0.0), cap) if value is not None else default
    except ValueError:
        return default


def history(s, room: str = "general") -> list[dict]:
    with httpx.Client(trust_env=False, timeout=5) as c:
        r = c.get(f"{s.relay_url}/rooms/{room}/history", params={"limit": 200},
                  headers={"Authorization": f"Bearer {s.secret}"})
    if r.status_code == 429:
        raise RelayBusy(retry_after(r.headers.get("Retry-After")))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("messages", [])


def wait_for(s, sender: str, marker: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for m in history(s):
                if m.get("from_name") == sender and marker in (m.get("content") or ""):
                    return m["content"]
            pause = POLL_S
        except RelayBusy as busy:  # back off as the relay asks, never hammer it
            pause = max(busy.wait, POLL_S)
        time.sleep(max(0.0, min(pause, deadline - time.monotonic())))
    try:
        seen = [(m.get("from_name"), (m.get("content") or "")[:80]) for m in history(s)]
    except RelayBusy:
        seen = "(relay busy: 429)"
    raise AssertionError(f"no message from {sender} with {marker!r} within {timeout}s; "
                         f"history: {seen}\n{agent_logs(s.home, sender)}")


def agent_logs(home: Path, name: str, lines: int = 40) -> str:
    """Why an agent never answered, read before teardown removes it: its daemon log
    and crash log, or a note that it was never started (an orphan of the same name
    once made `agent add` skip the start: 6fd323c1)."""
    agents = home / ".quorus" / "agents"
    found = [p for p in (agents / f"{name}.log", agents / f"{name}.crash.log") if p.exists()]
    if not found:
        return f"no daemon log for {name} in {agents}: it was never started"
    return "\n".join(f"--- {p.name} (last {lines} lines)\n"
                     + "\n".join(p.read_text(errors="replace").splitlines()[-lines:])
                     for p in found)


def calls(home: Path) -> list[dict]:
    try:
        return [json.loads(ln) for ln in (home / "stub-calls.jsonl").read_text().splitlines()]
    except FileNotFoundError:
        return []
