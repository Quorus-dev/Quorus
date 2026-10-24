"""Bet 1's harness (docs/internal/bets/lab-top3.md): from a clean HOME, does a
stranger get a first real result, and how many commands does it take?

Each persona is someone with one agent CLI installed, standing in their own repo.
They run plain `quorus`, answer the wizard (y to edits), run the "Try it" line
the wizard printed, then ask for a fix. The result counts only when it's really
there: a commit on main, whose checkout passes the repo's own test, made with the
write access the daemon granted (codex, review of the bet). A printed next step
that fails is a dead end and fails the test.

Stub CLIs (`stub_agents.py`) make it free and offline, so the wall time here is
setup time, never time to first result. Set QUORUS_FIRST_RESULT_REPORT to a file
to append each persona's command count and setup time as a JSON line.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fcntl", reason="needs a POSIX pty")
from tests.firstrun.pty_session import ENTER, PtySession, child_env, plain
from tests.firstrun.stub_agents import OWNER, calls, git, relay_secret, setup, wait_for

pytestmark = pytest.mark.firstrun

_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_TRY_IT = re.compile(r'Try it: (quorus say \S+ "[^"]+")')
_RUN = "import sys; sys.argv = ['quorus'] + sys.argv[1:]; from quorus_cli.cli import main; main()"
MAX_COMMANDS = 4  # bet 1's target, counting `pipx install quorus-ai`


def _quorus(s: PtySession, bin_dir: Path | None, command: str) -> subprocess.CompletedProcess:
    """Run a printed `quorus ...` line the way the user would, in their shell."""
    argv = shlex.split(command)
    assert argv[0] == "quorus", command
    return subprocess.run([sys.executable, "-c", _RUN, *argv[1:]], cwd=s.cwd,
                          env=child_env(s.home, s.relay_url, bin_dir),
                          capture_output=True, text=True, timeout=60)


def _report(persona: str, commands: list[str], seconds: float) -> None:
    path = os.environ.get("QUORUS_FIRST_RESULT_REPORT")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps({"persona": persona, "commands": len(commands),
                                "typed": commands, "setup_seconds": round(seconds, 1)}) + "\n")


def _fix_lands(s: PtySession, agent: str, home: Path, repo: Path) -> None:
    """The agent's ✅ names a commit that is main, and a fresh checkout of main
    passes the repo's own test: really there, not just said."""
    fixed = wait_for(s, agent, "fixed add()", timeout=120)  # not its ✅ on a review
    sha = _SHA.search(fixed.split("✅", 1)[1]).group(0)
    deadline = time.monotonic() + 30
    while git(repo, "rev-parse", "--short", "main")[:7] != sha[:7]:
        assert time.monotonic() < deadline, f"main is {git(repo, 'log', '--oneline', '-3')}"
        time.sleep(0.5)
    checkout = home / "checkout"
    subprocess.run(["git", "clone", "-q", str(repo), str(checkout)], check=True)
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "test_calc.py"], cwd=checkout, capture_output=True, text=True,
                           env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"})
    assert tests.returncode == 0, tests.stdout + tests.stderr


@pytest.mark.parametrize("tool", ["codex", "opencode"])
def test_one_agent_cli_gets_a_tested_fix_on_main(tmp_path_factory, tool):
    home = tmp_path_factory.mktemp(f"first-{tool}") / "home"
    repo, bin_dir = setup(home, (tool,))
    agent = f"{OWNER}-{tool}"
    commands = ["pipx install quorus-ai", "quorus"]
    started = time.monotonic()
    s = PtySession(home, cwd=repo, extra_path=bin_dir)
    try:
        s.expect(r"Your name", timeout=30)
        s.type(OWNER + ENTER)
        s.expect(r"Invite, or Enter to run it here")
        s.type(ENTER)
        s.expect(r"Add them to #general", timeout=45)
        s.type("y" + ENTER)
        s.expect(r"\[y/n\] \(n\)", timeout=20)
        s.type("y" + ENTER)
        screen = s.expect(r"You're in as @" + OWNER, timeout=90)
        s.settle(quiet=1.0, timeout=10)
        s.secret = relay_secret(home)

        # The wizard's own next step must work (a dead end otherwise).
        try_it = _TRY_IT.search(screen.replace("\n", " "))
        assert try_it, f"the wizard printed no Try it line:\n{screen[-1500:]}"
        commands.append(try_it.group(1))
        done = _quorus(s, bin_dir, try_it.group(1))
        assert done.returncode == 0, (done.stdout, done.stderr)
        wait_for(s, agent, "Hi:", timeout=90)

        ask = f"@{agent} fix the bug in calc.py"
        commands.append(ask)
        s.type(ask + ENTER)
        _fix_lands(s, agent, home, repo)
        seconds = time.monotonic() - started

        # ...made with the write access the daemon granted, not a stub's claim.
        turns = calls(home)
        assert len(turns) == 2 and turns[-1]["can_write"] is True, turns  # hello, then fix
        assert len(commands) <= MAX_COMMANDS, commands
        _report(f"{tool}-only", commands, seconds)
    finally:
        s.close()


_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")


def _invitee(base: Path, add: str) -> tuple[PtySession, PtySession, Path, Path, str]:
    """alice runs her first run and shares #general; bob, with Codex installed and
    standing in his own repo, joins with her code and answers the wizard's offer
    of his agent with *add* ("y" also lets it edit, after the disclosure). Returns
    both sessions, bob's repo and stub dir, and bob's wizard screen."""
    home_a, home_b = base / "alice", base / "bob"
    repo, bin_dir = setup(home_b, ("codex",), owner="bob")
    alice = PtySession(home_a)
    bob = None
    # Close what started if any step fails: the caller only sees sessions this
    # returns, and a failure here once left alice's relay running overnight
    # (overseer's orphan sweep, 2026-10-20: two invitee relays from 20:41).
    try:
        alice.expect(r"Your name", timeout=30)
        alice.type("alice" + ENTER)
        alice.expect(r"Invite, or Enter to run it here")
        alice.type(ENTER)
        alice.expect(r"You're in as @alice", timeout=90)
        alice.secret = relay_secret(home_a)
        shared = _quorus(alice, None, "quorus share general")
        assert shared.returncode == 0, (shared.stdout, shared.stderr)
        code = _CODE.search(shared.stdout)
        assert code, shared.stdout
        bob = PtySession(home_b, cwd=repo, extra_path=bin_dir, relay_url=alice.relay_url)
        bob.expect(r"Your name", timeout=30)
        bob.type("bob" + ENTER)
        bob.expect(r"Invite, or Enter to run it here")
        bob.type(code.group(1) + ENTER)
        bob.expect(r"relay address", timeout=20)
        bob.type(alice.relay_url + ENTER)
        bob.expect(r"Add them to #general", timeout=60)
        bob.type(add + ENTER)
        if add == "y":
            screen = bob.expect(r"\[y/n\] \(n\)", timeout=30)
            assert "teammate's relay" in screen and "your own key" in screen, screen[-1500:]
            bob.type("y" + ENTER)
        bob.expect(r"You're in as @bob", timeout=90)
        bob.settle(quiet=1.0, timeout=10)
        return alice, bob, repo, bin_dir, plain(bob.text())
    except BaseException:
        for s in (bob, alice):
            if s is None:
                continue
            try:
                s.close()
            except Exception:
                pass  # the original failure is the one to report; still close the other
        raise


def test_an_invitee_adds_their_agent_from_the_wizard(tmp_path_factory):
    """bob's wizard offers the Codex it found for the room he was invited to, says
    whose relay it is, and his first request lands as a tested commit: three
    commands, counting the install."""
    started = time.monotonic()
    alice = bob = None
    try:
        alice, bob, repo, _bin, _screen = _invitee(tmp_path_factory.mktemp("invitee-wiz"), "y")
        bob.secret = alice.secret
        ask = "@bob-codex fix the bug in calc.py"
        commands = ["pipx install quorus-ai", "quorus", ask]
        bob.type(ask + ENTER)
        _fix_lands(alice, "bob-codex", bob.home, repo)
        assert calls(bob.home)[-1]["can_write"] is True, calls(bob.home)
        assert len(commands) <= MAX_COMMANDS, commands
        _report("invitee-wizard", commands, time.monotonic() - started)
    finally:
        for s in (bob, alice):
            if s is not None:
                s.close()


def _logs(*homes: Path, tail: int = 60) -> str:
    """The tail of every agent and relay log under *homes*, for a failure message."""
    out = []
    for home in homes:
        for log in sorted((home / ".quorus").rglob("*.log")):
            lines = log.read_text(errors="replace").splitlines()[-tail:]
            out.append(f"--- {log.relative_to(home)}\n" + "\n".join(lines))
    return "\n".join(out) or "(no logs)"


def test_an_invitee_gets_a_tested_fix_in_their_own_repo(tmp_path_factory):
    """bob joins alice's relay with her invite and gets his own agent to fix his own
    repo, with the command /install gives (step 2). alice owns the relay, not bob's
    agent: her request to it gets a read-only turn (0.6.0's cross-company rule)."""
    started = time.monotonic()
    alice = bob = None
    try:
        alice, bob, repo, bin_dir, _screen = _invitee(tmp_path_factory.mktemp("invitee"), "n")
        add = f"quorus agent add codex --room general --repo {repo} --mode autonomous"
        commands = ["pipx install quorus-ai", "quorus", add]
        done = _quorus(bob, bin_dir, add)
        assert done.returncode == 0, (done.stdout, done.stderr)

        _quorus(alice, None, 'quorus say general "@bob-codex fix the bug in calc.py"')
        try:
            wait_for(alice, "bob-codex", "read-only", timeout=120)
        except AssertionError as exc:  # say why the agent was silent (PR #22, Linux CI)
            raise AssertionError(f"{exc}\n{_logs(bob.home, alice.home)}") from None
        # alice holds the relay secret, but bob's name is an invited member's: the
        # relay won't let her post as him (name reservations). If it ever did, bob's
        # agent would still trust only bob's own key (tests/test_owner_pin.py).
        with httpx.Client(trust_env=False, timeout=5) as c:
            forged = c.post(f"{alice.relay_url}/rooms/general/messages",
                            headers={"Authorization": f"Bearer {alice.secret}"},
                            json={"from_name": "bob", "content": "@bob-codex fix calc.py"})
        assert forged.status_code == 409, forged.text
        assert "return a - b" in git(repo, "show", "main:calc.py")

        ask = "@bob-codex fix the bug in calc.py"
        commands.append(ask)
        bob.type(ask + ENTER)
        _fix_lands(alice, "bob-codex", bob.home, repo)
        seconds = time.monotonic() - started

        turns = calls(bob.home)
        assert len(turns) == 2, turns  # alice's request, then bob's
        assert turns[0]["untrusted"] is True and turns[0]["can_write"] is False, turns
        assert turns[-1]["untrusted"] is False and turns[-1]["can_write"] is True, turns
        assert len(commands) <= MAX_COMMANDS, commands
        _report("invitee", commands, seconds)
    finally:
        for s in (bob, alice):
            if s is not None:
                s.close()


def test_a_failure_inside_the_invitee_setup_leaves_no_relay_behind(tmp_path_factory, monkeypatch):
    """Overseer's orphan sweep (2026-10-20): two invitee relays from a 20:41 run were
    still serving the next morning. `_invitee` starts alice's relay before any test
    `try/finally` exists, so a failing share used to leak it. Now the helper closes
    every session it opened before re-raising."""
    from tests.firstrun import pty_session

    base = tmp_path_factory.mktemp("invitee-leak")
    monkeypatch.setattr(sys.modules[__name__], "_quorus",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "share broke"))
    with pytest.raises(AssertionError, match="share broke"):
        _invitee(base, "n")
    started = pty_session.spawned_relays(base / "alice")
    assert started, "the helper never got as far as starting alice's relay"
    still_up = [pid for pid in started if _alive(pid)]
    assert still_up == [], f"invitee relay(s) survived the failed setup: {still_up}"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
