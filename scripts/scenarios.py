#!/usr/bin/env python3
"""Real-world scenario gate for Quorus — real Claude + Codex, real CLI.

Simulates a new user end to end on an ISOLATED relay (own port, own state,
identities qa / bob / qa-claude / qa-codex), driving only the commands a user
would type. Each scenario asserts an observable outcome (room replies, commits
on main, approvals, silence where silence is right) with generous timeouts,
because real models are slow.

    .venv/bin/python scripts/scenarios.py            # everything (~45-90 min)
    .venv/bin/python scripts/scenarios.py S2 S8      # a subset
    .venv/bin/python scripts/scenarios.py --keep     # leave state for debugging

Never touches your own identities, rooms or dogfood daemons. Uses your logged
in `claude` / `codex` CLIs (that is the point), so it spends real usage.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
VB = REPO / ".venv" / "bin"
PORT = int(os.environ.get("QUORUS_QA_PORT", "47900"))
URL = f"http://127.0.0.1:{PORT}"
SECRET = "qa-secret-" + os.urandom(6).hex()
W = Path(tempfile.mkdtemp(prefix="quorus-qa-"))
ROOM = f"qa-{os.urandom(2).hex()}"          # main room
ROOM2 = ROOM + "-b"                          # second project room
ROOM3 = ROOM + "-safe"                       # manual-mode room
ROOM4 = ROOM + "-nobind"                     # unbound room
BINDINGS = Path.home() / ".quorus" / "room-bindings.json"
RESULTS: list[dict[str, Any]] = []


# ── plumbing ────────────────────────────────────────────────────────────────

def sh(*argv: str, env: dict[str, str] | None = None, cwd: Path | None = None,
       check: bool = False, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(list(argv), env={**os.environ, **(env or {})}, cwd=cwd,
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} -> {r.returncode}: {r.stderr[-400:]}")
    return r


def human_env(name: str) -> dict[str, str]:
    return {"QUORUS_CONFIG_DIR": str(W / name), "QUORUS_API_KEY": "", "API_KEY": ""}


def q(name: str, *args: str, check: bool = True, timeout: int = 120) -> str:
    """Run the `quorus` CLI as human *name*."""
    r = sh(str(VB / "quorus"), *args, env=human_env(name), cwd=Path("/tmp"),
           check=check, timeout=timeout)
    return (r.stdout + r.stderr).strip()


def api(path: str, method: str = "GET", body: dict | None = None) -> Any:
    req = urllib.request.Request(
        URL + path, method=method, data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read() or b"null")


def history(room: str) -> list[dict[str, Any]]:
    return api(f"/rooms/{room}/history?limit=300")


def say(room: str, text: str, who: str = "qa") -> str:
    """Post as a human; return the new message id."""
    before = {m["id"] for m in history(room)}
    q(who, "say", room, text)
    for _ in range(20):
        new = [m for m in history(room) if m["id"] not in before and m["from_name"] == who]
        if new:
            return new[-1]["id"]
        time.sleep(0.3)
    raise RuntimeError("posted message never appeared")


def agent_msgs_since(room: str, t0: str, agents: tuple[str, ...] = ("qa-claude", "qa-codex"),
                     ) -> list[dict[str, Any]]:
    return [m for m in history(room) if m["from_name"] in agents and m["timestamp"] > t0]


def now_iso() -> str:
    return api("/health").get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def wait(pred: Callable[[], Any], timeout: float, every: float = 4.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        got = pred()
        if got:
            return got
        time.sleep(every)
    return None


def git(repo: Path, *args: str) -> str:
    return sh("git", *args, cwd=repo, check=True).stdout.strip()


def commits(repo: Path) -> int:
    return int(git(repo, "rev-list", "--count", "main"))


def tests_pass(repo: Path) -> bool:
    return sh(sys.executable, "-m", "pytest", "-q", cwd=repo, timeout=300).returncode == 0


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def scenario(sid: str, title: str) -> Callable[[Callable[[], str]], Callable[[], None]]:
    def deco(fn: Callable[[], str]) -> Callable[[], None]:
        def run() -> None:
            t = time.time()
            try:
                detail = fn()
                ok = True
            except AssertionError as exc:
                ok, detail = False, f"ASSERT: {exc}"
            except Exception as exc:
                ok, detail = False, f"{exc.__class__.__name__}: {exc}"
            RESULTS.append({"id": sid, "title": title, "ok": ok, "detail": detail,
                            "secs": round(time.time() - t)})
            print(f"{'PASS' if ok else 'FAIL'} {sid} {title} ({round(time.time() - t)}s) "
                  f"— {detail}", flush=True)
        run.sid = sid  # type: ignore[attr-defined]
        return run
    return deco


# ── fixtures ────────────────────────────────────────────────────────────────

def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (path / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (path / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    g = ["-c", "user.email=qa@quorus.dev", "-c", "user.name=qa"]
    git(path, "init", "-q", "-b", "main")
    git(path, "add", ".")
    git(path, *g, "commit", "-q", "-m", "init")
    return path


def setup() -> None:
    print(f"workdir {W}\nrelay {URL} rooms {ROOM},{ROOM2},{ROOM3},{ROOM4}", flush=True)
    relay_log = (W / "relay.log").open("w")
    subprocess.Popen([str(VB / "quorus-relay")], stdout=relay_log, stderr=relay_log, env={
        **os.environ, "PORT": str(PORT), "HOST": "127.0.0.1", "RELAY_SECRET": SECRET,
        "ALLOW_LEGACY_AUTH": "1", "MESSAGES_FILE": str(W / "state.json"),
        "LOG_LEVEL": "WARNING"})
    assert wait(lambda: _healthy(), 30, 0.5), "relay never became healthy"
    make_repo(W / "app")
    make_repo(W / "app2")


def _healthy() -> bool:
    try:
        return bool(api("/health"))
    except Exception:
        return False


def teardown(keep: bool) -> None:
    for agent in ("qa-claude", "qa-codex", "qa-gemini"):
        q("qa", "agent", "remove", agent, check=False)
    try:
        data = json.loads(BINDINGS.read_text())
        for r in (ROOM, ROOM2, ROOM3, ROOM4):
            data.pop(r, None)
        BINDINGS.write_text(json.dumps(data, indent=2))
    except (OSError, ValueError):
        pass
    sh("pkill", "-f", f"PORT={PORT}", check=False)
    for pid in sh("pgrep", "-f", str(VB / "quorus-relay")).stdout.split():
        env = sh("ps", "eww", "-p", pid).stdout
        if f"PORT={PORT}" in env:
            sh("kill", pid)
    for wt in (W / ".quorus-worktrees",):
        shutil.rmtree(wt, ignore_errors=True)
    if not keep:
        shutil.rmtree(W, ignore_errors=True)


# ── scenarios ───────────────────────────────────────────────────────────────

@scenario("S1", "new user: init, create room, add Claude + Codex")
def s1() -> str:
    home = W / "qa-home"
    home.mkdir()
    r = sh(str(VB / "quorus"), "init", "qa", "--relay-url", URL, "--secret", SECRET,
           "--no-autostart", "--no-smoke", "--no-launchd",
           env={**human_env("qa"), "HOME": str(home)}, cwd=Path("/tmp"))
    assert r.returncode == 0, r.stderr[-300:]
    assert "@qa" in q("qa", "whoami")
    assert "Created" in q("qa", "create", ROOM)
    out = q("qa", "agent", "add", "claude", "--room", ROOM, "--repo", str(W / "app"),
            "--mode", "autonomous")
    assert "joined" in out and "running" in out, out
    out = q("qa", "agent", "add", "codex", "--room", ROOM)
    assert "joined" in out, out
    members = q("qa", "members", ROOM)
    assert "qa-claude" in members and "qa-codex" in members, members
    assert wait(lambda: _connected("qa-claude") and _connected("qa-codex"), 60), \
        "daemons never connected"
    return "init/create/agent add/members ok; both daemons connected"


def _connected(agent: str) -> bool:
    room = api(f"/rooms/{ROOM}")
    return (room.get("member_presence", {}).get(agent, {}) or {}).get("presence") == "active"


@scenario("S2", "greeting to the room gets exactly one answer")
def s2() -> str:
    t0 = ts()
    say(ROOM, "hello team")
    got = wait(lambda: agent_msgs_since(ROOM, t0), 180)
    assert got, "no agent answered a human greeting"
    time.sleep(40)
    n = len(agent_msgs_since(ROOM, t0))
    assert n == 1, f"expected 1 answer, got {n}"
    return f"answered by {got[0]['from_name']}"


@scenario("S3", "acknowledgement stays silent")
def s3() -> str:
    t0 = ts()
    say(ROOM, "thanks")
    time.sleep(45)
    n = len(agent_msgs_since(ROOM, t0))
    assert n == 0, f"{n} agent replies to 'thanks'"
    return "no replies"


@scenario("S4", "question ending in ? gets one answer that reads the repo")
def s4() -> str:
    t0 = ts()
    say(ROOM, "what does the add function in calc.py return for 2 and 3?")
    got = wait(lambda: agent_msgs_since(ROOM, t0), 240)
    assert got, "no answer"
    assert "5" in got[0]["content"], got[0]["content"][:200]
    return f"{got[0]['from_name']}: {got[0]['content'][:80]!r}"


@scenario("S5", "@mention wakes only that agent (Claude)")
def s5() -> str:
    t0 = ts()
    say(ROOM, "@qa-claude reply with the single word PONG")
    got = wait(lambda: agent_msgs_since(ROOM, t0, ("qa-claude",)), 240)
    assert got and "PONG" in got[0]["content"].upper(), got and got[0]["content"][:120]
    time.sleep(20)
    assert not agent_msgs_since(ROOM, t0, ("qa-codex",)), "codex replied to a claude mention"
    return "claude answered, codex silent"


@scenario("S6", "@mention wakes only that agent (Codex)")
def s6() -> str:
    t0 = ts()
    say(ROOM, "@qa-codex reply with the single word PONG")
    got = wait(lambda: agent_msgs_since(ROOM, t0, ("qa-codex",)), 240)
    assert got and "PONG" in got[0]["content"].upper(), got and got[0]["content"][:120]
    time.sleep(20)
    assert not agent_msgs_since(ROOM, t0, ("qa-claude",)), "claude replied to a codex mention"
    return "codex answered, claude silent"


@scenario("S7", "message to another human: agents stay out")
def s7() -> str:
    t0 = ts()
    say(ROOM, "@bob can you look at the invoices later")
    time.sleep(45)
    n = len(agent_msgs_since(ROOM, t0))
    assert n == 0, f"{n} agent replies to a message for @bob"
    return "silent"


@scenario("S8", "@open build task: built, tested, published, reviewed")
def s8() -> str:
    repo = W / "app"
    c0, t0 = commits(repo), ts()
    say(ROOM, "@open add a multiply(a, b) function to calc.py with a pytest test. "
              "Commit when green, then ask a teammate to review your commit.")
    assert wait(lambda: commits(repo) > c0, 480), "nothing published to main"
    assert "multiply" in (repo / "calc.py").read_text(), "main lacks multiply"
    assert tests_pass(repo), "tests fail on main"
    review = wait(lambda: [m for m in agent_msgs_since(ROOM, t0, ("qa-codex",))], 480)
    assert review, "no review from the other agent"
    return f"main +{commits(repo) - c0} commit(s), tests green, codex reviewed"


@scenario("S9", "two @open tasks on the same file in parallel both land")
def s9() -> str:
    repo = W / "app"
    c0 = commits(repo)
    say(ROOM, "@open add subtract(a, b) to calc.py with a test. Commit when green.")
    say(ROOM, "@open add divide(a, b) to calc.py (raise ValueError on zero) with tests. "
              "Commit when green.")
    def both() -> bool:
        text = (repo / "calc.py").read_text()
        return "subtract" in text and "divide" in text
    assert wait(both, 900), "main has: " + ", ".join(
        line.split("(")[0][4:] for line in (repo / "calc.py").read_text().splitlines()
        if line.startswith("def "))
    assert tests_pass(repo), "tests fail on main after parallel work"
    return f"both functions on main (+{commits(repo) - c0} commits), tests green"


@scenario("S10", "one agent, two projects: work lands in the right repo")
def s10() -> str:
    q("qa", "create", ROOM2)
    q("qa", "agent", "add", "claude", "--room", ROOM2, "--repo", str(W / "app2"),
      "--mode", "autonomous")
    a1, a2 = commits(W / "app"), commits(W / "app2")
    say(ROOM2, "@qa-claude add a file NOTES.md containing the word project-two and commit it")
    assert wait(lambda: (W / "app2" / "NOTES.md").exists(), 400), "app2 never got NOTES.md"
    assert not (W / "app" / "NOTES.md").exists(), "NOTES.md leaked into the other repo"
    assert commits(W / "app") == a1 and commits(W / "app2") > a2
    return "app2 got the commit, app untouched"


@scenario("S11", "manual mode: agent asks its owner; approve and deny both work")
def s11() -> str:
    q("qa", "create", ROOM3)
    repo = make_repo(W / "app3")
    q("qa", "agent", "add", "claude", "--room", ROOM3, "--repo", str(repo), "--mode", "manual")
    t0 = ts()
    say(ROOM3, "@qa-claude run the shell command `touch approved.txt` in the repo, "
               "then tell me it is done")
    pending = wait(lambda: _pending(ROOM3, "approved.txt"), 300)
    assert pending, "no approval request reached the room"
    # another human may not decide it
    q("bob", "join", ROOM3, check=False)
    other = q("bob", "approve", pending[0]["id"], check=False)
    assert "approved" not in other.lower() or "only" in other.lower(), other
    out = q("qa", "approve", pending[0]["id"])
    assert "approv" in out.lower(), out
    assert wait(lambda: _worktree_file(repo, "qa-claude", "approved.txt") or
                (repo / "approved.txt").exists(), 300), "approved command never ran"
    # deny path
    say(ROOM3, "@qa-claude run the shell command `touch denied.txt` in the repo")
    pending = wait(lambda: _pending(ROOM3, "denied.txt"), 300)
    assert pending, "second approval request missing"
    q("qa", "deny", pending[0]["id"])
    reply = wait(lambda: [m for m in agent_msgs_since(ROOM3, t0, ("qa-claude",))
                          if "denied" in m["content"].lower() or "deny" in m["content"].lower()
                          or "not" in m["content"].lower()], 300)
    assert not _worktree_file(repo, "qa-claude", "denied.txt"), "denied command ran anyway"
    for extra in _pending(ROOM3):  # answer stragglers so the agent isn't held
        q("qa", "deny", extra["id"], check=False)
    return f"approve ran it; bob could not decide; deny blocked it ({bool(reply)})"


def _pending(room: str, about: str = "") -> list[dict[str, Any]]:
    """Pending approvals in *room*, optionally only those whose request
    mentions *about* (agents may ask for extra, unrelated permissions)."""
    data = api(f"/v1/approvals?room={room}")
    return [a for a in (data or {}).get("pending", [])
            if a.get("status", "pending") == "pending" and about in json.dumps(a)]


def _worktree_file(repo: Path, agent: str, name: str) -> bool:
    return (repo.parent / ".quorus-worktrees" / repo.name / agent / name).exists()


@scenario("S12", "unbound room: agent says it needs a repo instead of guessing")
def s12() -> str:
    q("qa", "create", ROOM4)
    q("qa", "agent", "add", "claude", "--room", ROOM4)
    t0 = ts()
    say(ROOM4, "@qa-claude add a README to the project")
    got = wait(lambda: agent_msgs_since(ROOM4, t0, ("qa-claude",)), 240)
    assert got, "no reply"
    text = got[0]["content"].lower()
    assert "bind" in text or "repo" in text or "workspace" in text, text[:200]
    return "asked for a bound repo"


@scenario("S13", "agent asleep (daemon down) when a task arrives: picked up on wake")
def s13() -> str:
    label = f"gui/{os.getuid()}/dev.quorus.agent.qa-claude"
    sh("launchctl", "bootout", label)
    time.sleep(3)
    t0 = ts()
    say(ROOM, "@qa-claude reply with the single word AWAKE")
    time.sleep(10)
    plist = Path.home() / "Library" / "LaunchAgents" / "dev.quorus.agent.qa-claude.plist"
    sh("launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist), check=True)
    got = wait(lambda: [m for m in agent_msgs_since(ROOM, t0, ("qa-claude",))
                        if "AWAKE" in m["content"].upper()], 300)
    assert got, "queued mention was not handled after the agent came back"
    return "handled after restart"


@scenario("S14", "restart mid-job: job is redelivered and finished")
def s14() -> str:
    repo = W / "app"
    t0 = ts()
    say(ROOM, "@qa-claude add a power(a, b) function to calc.py with a test and commit it")
    started = wait(lambda: "power" in _agent_log_since("qa-claude", t0) or
                   _harness_running(), 120, 2)
    assert started, "job never started"
    sh("launchctl", "kickstart", "-k", f"gui/{os.getuid()}/dev.quorus.agent.qa-claude")
    assert wait(lambda: "power" in (repo / "calc.py").read_text(), 600), \
        "job was lost by the restart"
    assert tests_pass(repo)
    return "redelivered and published after restart"


def _agent_log_since(agent: str, t0: str) -> str:
    log = Path.home() / ".quorus" / "reflexd.log"
    try:
        return "\n".join(line for line in log.read_text(errors="replace").splitlines()[-400:]
                         if agent in line)
    except OSError:
        return ""


def _harness_running() -> bool:
    return bool(sh("pgrep", "-f", "claude --print").stdout.strip())


@scenario("S15", "relay restart: rooms and history survive, agents reconnect")
def s15() -> str:
    n0 = len(history(ROOM))
    old = [pid for pid in sh("pgrep", "-f", str(VB / "quorus-relay")).stdout.split()
           if f"PORT={PORT}" in sh("ps", "eww", "-p", pid).stdout]
    for pid in old:
        sh("kill", pid)
    # launchd/systemd wait for the old process to exit before restarting it;
    # do the same, or the new relay loads the file before the final save.
    assert wait(lambda: not any(sh("kill", "-0", p).returncode == 0 for p in old), 30, 0.5), \
        "old relay did not exit within 30s of SIGTERM"
    relay_log = (W / "relay2.log").open("w")
    subprocess.Popen([str(VB / "quorus-relay")], stdout=relay_log, stderr=relay_log, env={
        **os.environ, "PORT": str(PORT), "HOST": "127.0.0.1", "RELAY_SECRET": SECRET,
        "ALLOW_LEGACY_AUTH": "1", "MESSAGES_FILE": str(W / "state.json"),
        "LOG_LEVEL": "WARNING"})
    assert wait(_healthy, 30, 0.5), "relay did not come back"
    assert len(history(ROOM)) >= n0, "history lost on relay restart"
    t0 = ts()
    time.sleep(10)
    say(ROOM, "@qa-codex reply with the single word BACK")
    got = wait(lambda: agent_msgs_since(ROOM, t0, ("qa-codex",)), 300)
    assert got, "agent did not reconnect after relay restart"
    return f"history kept ({len(history(ROOM))} msgs), codex answered after restart"


@scenario("S16", "second human: their greeting is answered; can't approve my agent")
def s16() -> str:
    home = W / "bob-home"
    home.mkdir(exist_ok=True)
    sh(str(VB / "quorus"), "init", "bob", "--relay-url", URL, "--secret", SECRET,
       "--no-autostart", "--no-smoke", "--no-launchd",
       env={**human_env("bob"), "HOME": str(home)}, cwd=Path("/tmp"), check=True)
    q("bob", "join", ROOM)
    t0 = ts()
    say(ROOM, "hi everyone, bob here", who="bob")
    got = wait(lambda: agent_msgs_since(ROOM, t0), 240)
    assert got, "bob's greeting was ignored"
    rec = api("/v1/approvals", "POST", {"room_id": ROOM, "agent": "qa-claude",
                                        "tool_name": "Bash", "tool_input": "ls"})
    out = q("bob", "approve", rec["id"], check=False)
    status = api(f"/v1/approvals/{rec['id']}")["status"]
    assert status == "pending", f"bob decided qa's agent approval: {status} / {out[:120]}"
    return "bob answered; bob cannot approve qa-claude"


@scenario("S17", "agents can't ping-pong forever")
def s17() -> str:
    t0 = ts()
    say(ROOM, "@qa-claude ask @qa-codex a question, and both of you keep asking each "
              "other questions back and forth forever")
    time.sleep(360)
    n = len(agent_msgs_since(ROOM, t0))
    assert n <= 14, f"{n} agent messages in 6 minutes — loop guard failed"
    return f"{n} agent messages, then stopped"


@scenario("S18", "burst of human messages: no storm, daemon healthy")
def s18() -> str:
    t0 = ts()
    for i in range(6):
        q("qa", "say", ROOM, f"status ping {i}: ok")  # statements, not requests
    time.sleep(60)
    n = len(agent_msgs_since(ROOM, t0))
    assert n <= 1, f"{n} replies to six status statements"
    assert _connected("qa-claude") and _connected("qa-codex"), "a daemon went away"
    return f"{n} replies, both daemons still active"


@scenario("S19", "CLI error paths are clear")
def s19() -> str:
    out = q("qa", "agent", "add", "claude", "--room", "no-such-room-xyz", check=False)
    assert "not found" in out.lower(), out[-200:]
    out = q("qa", "room", "bind", ROOM, "/definitely/missing/dir", check=False)
    assert "not a directory" in out.lower(), out[-200:]
    out = q("qa", "agent", "add", "notatool", "--room", ROOM, check=False)
    assert "invalid choice" in out.lower(), out[-200:]
    return "missing room, bad path, bad tool all explained"


@scenario("S20", "agent remove: stops it and takes it out of every room")
def s20() -> str:
    out = q("qa", "agent", "remove", "qa-codex")
    assert "stopped" in out, out
    rooms = [r for r in api("/rooms") if r["name"] in (ROOM, ROOM2, ROOM3, ROOM4)]
    assert all("qa-codex" not in (r.get("members") or []) for r in rooms), "still a member"
    t0 = ts()
    say(ROOM, "hello again team")
    got = wait(lambda: agent_msgs_since(ROOM, t0), 240)
    assert got and all(m["from_name"] == "qa-claude" for m in got)
    return "removed from rooms; only claude answers now"


SCENARIOS = [s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12, s13, s14, s15, s16,
             s17, s18, s19, s20]


def main() -> int:
    keep = "--keep" in sys.argv
    wanted = {a for a in sys.argv[1:] if a.startswith("S")}
    setup()
    try:
        for sc in SCENARIOS:
            if wanted and sc.sid not in wanted and sc.sid != "S1":
                continue
            sc()
            if sc.sid == "S1" and not RESULTS[-1]["ok"]:
                print("setup failed — stopping", flush=True)
                break
    finally:
        report = W.parent / f"quorus-qa-report-{int(time.time())}.json"
        report.write_text(json.dumps(RESULTS, indent=2))
        teardown(keep)
        passed = sum(r["ok"] for r in RESULTS)
        print(f"\n{passed}/{len(RESULTS)} scenarios passed — report {report}", flush=True)
    return 0 if RESULTS and all(r["ok"] for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
