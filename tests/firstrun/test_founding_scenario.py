"""The founding scenario: two people's agents in one room, from anywhere.

alice runs Quorus and serves her relay on her network; bob joins from his own
HOME over that non-loopback address with her invite and adds his own agent. Each
person's agent fixes its owner's repo, and the other person's agent reviews it.
Before 0.6.3 bob couldn't add an agent at all (0f9a90b7).

What counts, as in bet 1's harness: the fix is a commit on the owner's main whose
checkout passes the repo's test; the review names that commit; and the daemons'
launch arguments, not the stubs' replies, show that each agent got write access
only for its own owner's request. A request handed over by the other person's
agent stays read-only.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

pytest.importorskip("fcntl", reason="needs a POSIX pty")
from tests.firstrun.pty_session import ENTER, PtySession, child_env
from tests.firstrun.stub_agents import calls, git, relay_secret, setup, wait_for
from tests.firstrun.test_first_result import _CODE, _fix_lands, _quorus

pytestmark = pytest.mark.firstrun
_RUN = "import sys; sys.argv = ['quorus'] + sys.argv[1:]; from quorus_cli.cli import main; main()"


def _lan_ip() -> str | None:
    """This machine's address on its network (no packet is sent), or None."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


def _healthy(url: str) -> bool:
    try:
        with httpx.Client(trust_env=False, timeout=1) as c:
            return c.get(f"{url}/health").status_code == 200
    except httpx.HTTPError:
        return False


def _serve_on_network(alice: PtySession, port: int, lan_url: str) -> subprocess.Popen:
    """What `quorus share` tells alice to do for a teammate on another computer:
    serve her relay on every interface. --replace stops the first run's relay."""
    log = (alice.home / "lan-relay.log").open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-c", _RUN, "relay", "--host", "0.0.0.0", "--port", str(port),
         "--replace"], cwd=alice.home, env=child_env(alice.home, alice.relay_url),
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 30
    while not _healthy(lan_url):
        assert proc.poll() is None, (alice.home / "lan-relay.log").read_text()[-1500:]
        assert time.monotonic() < deadline, "the relay never answered on the LAN address"
        time.sleep(0.25)
    return proc


def _stop(proc: subprocess.Popen | None) -> None:
    """Our own relay, by its own process group: never by pattern."""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def _review_of(s: PtySession, reviewer: str, sha: str) -> str:
    review = wait_for(s, reviewer, f"/vote approve {sha[:7]}", timeout=120)
    assert sha[:7] in review, review
    return review


def test_two_peoples_agents_build_and_review_each_others_work(tmp_path_factory):
    lan = _lan_ip()
    if lan is None:
        pytest.skip("no non-loopback address on this machine")
    base = tmp_path_factory.mktemp("founding")
    home_a, home_b = base / "alice", base / "bob"
    repo_a, bin_a = setup(home_a, ("codex",), owner="alice", reviewer="bob-codex")
    repo_b, bin_b = setup(home_b, ("codex",), owner="bob", reviewer="alice-codex")
    alice = bob = relay = None
    try:
        alice = PtySession(home_a, cwd=repo_a, extra_path=bin_a)
        alice.expect(r"Your name", timeout=30)
        alice.type("alice" + ENTER)
        alice.expect(r"Invite, or Enter to run it here")
        alice.type(ENTER)
        alice.expect(r"Add them to #general", timeout=45)
        alice.type("y" + ENTER)
        alice.expect(r"\[y/n\] \(n\)", timeout=20)
        alice.type("y" + ENTER)
        alice.expect(r"You're @alice in #", timeout=90)
        alice.settle(quiet=1.0, timeout=10)
        alice.secret = relay_secret(home_a)

        lan_url = f"http://{lan}:{alice.port}"
        relay = _serve_on_network(alice, alice.port, lan_url)
        shared = _quorus(alice, None, "quorus share general")
        assert shared.returncode == 0, (shared.stdout, shared.stderr)
        code = _CODE.search(shared.stdout)
        assert code, shared.stdout

        bob = PtySession(home_b, cwd=repo_b, extra_path=bin_b, relay_url=lan_url)
        bob.expect(r"Your name", timeout=30)
        bob.type("bob" + ENTER)
        bob.expect(r"Invite, or Enter to run it here")
        bob.type(code.group(1) + ENTER)
        bob.expect(r"relay address", timeout=20)
        bob.type(lan_url + ENTER)
        bob.expect(r"Add them to #general", timeout=60)
        bob.type("y" + ENTER)
        bob.expect(r"\[y/n\] \(n\)", timeout=30)
        bob.type("y" + ENTER)
        screen = bob.expect(r"You're @bob in #", timeout=90)
        assert "every agent hears you" in screen, screen[-2000:]  # bob-codex was added
        bob.settle(quiet=1.0, timeout=10)
        bob.secret = alice.secret  # the test reads history; bob never holds it

        # bob's agent fixes bob's repo; alice's agent reviews the commit.
        bob.type("@bob-codex fix the bug in calc.py" + ENTER)
        _fix_lands(bob, "bob-codex", home_b, repo_b)
        bob_sha = git(repo_b, "rev-parse", "main")
        _review_of(bob, "alice-codex", bob_sha)

        # alice's agent fixes alice's repo; bob's agent reviews the commit.
        alice.type("@alice-codex fix the bug in calc.py" + ENTER)
        _fix_lands(alice, "alice-codex", home_a, repo_a)
        alice_sha = git(repo_a, "rev-parse", "main")
        _review_of(alice, "bob-codex", alice_sha)

        # Write access only for the owner's own request; a handoff from the other
        # person's agent is a read-only turn, on both sides.
        for home, order in ((home_b, ("fix", "review")), (home_a, ("review", "fix"))):
            # An unaddressed post (a review vote) may wake the other agent to look
            # at it: read-only, and it stays quiet. Those aren't requests.
            picked = [t for t in calls(home) if t["picked"]]
            assert all(t["can_write"] is False for t in picked), picked
            turns = [t for t in calls(home) if not t["picked"]]
            assert len(turns) == 2, turns
            by_kind = dict(zip(order, turns))
            assert by_kind["fix"]["can_write"] is True, turns
            assert by_kind["fix"]["untrusted"] is False, turns
            assert by_kind["review"]["can_write"] is False, turns
            assert by_kind["review"]["from_agent"] is True, turns
        # Each repo has exactly its owner's fix: nobody wrote into the other's.
        for repo in (repo_a, repo_b):
            assert git(repo, "log", "--format=%s", "main").splitlines() == [
                "fix: add() returns the sum", "calc with a bug"]
    finally:
        for s in (bob, alice):
            if s is not None:
                s.close()
        _stop(relay)
