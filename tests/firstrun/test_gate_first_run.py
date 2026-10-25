"""Gate by default, end to end (docs/plans/gate-by-default.md, Tests: the episode).

A first run in a git repo with stub codex (the lead) and opencode on a real local
relay. Enter takes the gate's default yes. The stranger-path ask gets a fix from
codex; its daemon holds the commit off main and tells @alice-opencode how to
approve it; opencode's GO lands it with "reviewed by @alice-opencode". Nothing
leaves the machine.
"""

from __future__ import annotations

import re
import time

import pytest

pytest.importorskip("fcntl", reason="needs a POSIX pty")
from tests.firstrun.pty_session import ENTER, PtySession
from tests.firstrun.stub_agents import git as _git
from tests.firstrun.stub_agents import history as _history
from tests.firstrun.stub_agents import landed as _landed
from tests.firstrun.stub_agents import setup as _setup
from tests.firstrun.stub_agents import wait_for as _wait_for
from tests.firstrun.test_f5_fix_lands import ASK, _first_run, _not_on_main

pytestmark = pytest.mark.firstrun
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def _wait_any(s, marker: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for m in _history(s):
            if marker in (m.get("content") or ""):
                return m
        time.sleep(0.5)
    seen = [(m.get("from_name"), (m.get("content") or "")[:120]) for m in _history(s)]
    raise AssertionError(f"no message with {marker!r} within {timeout}s; history: {seen}")


def test_the_default_first_run_holds_the_fix_until_the_other_vendor_goes(tmp_path_factory):
    home = tmp_path_factory.mktemp("gate-default") / "home"
    repo, bin_dir = _setup(home)
    s = PtySession(home, cwd=repo, extra_path=bin_dir)
    try:
        _first_run(s, home, repo, write_answer="y", gate="")  # Enter: the default yes
        line = _wait_any(s, "Review gate on:", timeout=30)
        assert line["from_name"] == "alice" and "@alice-opencode's GO" in line["content"]
        main_before = _git(repo, "rev-parse", "main")

        s.type(ASK + ENTER)
        fixed = _wait_for(s, "alice-codex", "✅", timeout=120)
        sha = _SHA.search(fixed.split("✅", 1)[1]).group(0)
        # Held, and the reviewer is told exactly how to let it through.
        held = _wait_any(s, "awaiting @alice-opencode's review", timeout=60)["content"]
        assert "✅ GO on " in held and sha[:7] in held, held

        go = _wait_for(s, "alice-opencode", "✅ GO on", timeout=120)
        assert sha[:7] in go
        deadline = time.monotonic() + 180  # the daemon's review tick, then the publish
        while _landed(repo, sha) is None:
            assert time.monotonic() < deadline, _not_on_main(repo, sha, home)
            time.sleep(0.5)
        assert "return a + b" in _git(repo, "show", "main:calc.py")
        assert _git(repo, "rev-parse", "main") != main_before
        note = _wait_any(s, "reviewed by @alice-opencode", timeout=60)["content"]
        assert "published" in note
        # Main moved only after the GO: in the room's order, held → GO → published,
        # and no publish note without the review (the stub GOs within seconds, so
        # polling main between the hold and the GO raced it).
        texts = [(m.get("from_name"), m.get("content") or "") for m in _history(s)]
        at = {k: next(i for i, (who, c) in enumerate(texts) if pick(who, c))
              for k, pick in {
                  "held": lambda w, c: "awaiting @alice-opencode's review" in c,
                  "go": lambda w, c: w == "alice-opencode" and "✅ GO on" in c,
                  "published": lambda w, c: "reviewed by @alice-opencode" in c}.items()}
        assert at["held"] < at["go"] < at["published"], (at, texts)
        unreviewed = [c for _, c in texts if "(quorus) published" in c and "reviewed by" not in c]
        assert not unreviewed, unreviewed
    finally:
        s.close()


def test_the_reviewers_own_fix_waits_for_the_leads_go(tmp_path_factory):
    """docs/plans/reviewer-backup.md: the wizard pins the lead (codex) to review the
    reviewer's (opencode's) own commits, so the owner isn't the only way through."""
    home = tmp_path_factory.mktemp("gate-second") / "home"
    repo, bin_dir = _setup(home)
    s = PtySession(home, cwd=repo, extra_path=bin_dir)
    try:
        _first_run(s, home, repo, write_answer="y", gate="")
        line = _wait_any(s, "Review gate on:", timeout=30)["content"]
        assert "@alice-opencode's own commits wait for @alice-codex's" in line
        ask = "@alice-opencode fix the bug in calc.py, then ask @alice-codex to review it"
        s.type(ask + ENTER)
        fixed = _wait_for(s, "alice-opencode", "✅", timeout=120)
        sha = _SHA.search(fixed.split("✅", 1)[1]).group(0)
        held = _wait_any(s, "awaiting @alice-codex's review", timeout=60)["content"]
        assert sha[:7] in held and "✅ GO on " in held
        go = _wait_for(s, "alice-codex", "✅ GO on", timeout=120)
        assert sha[:7] in go
        deadline = time.monotonic() + 180
        while _landed(repo, sha) is None:
            assert time.monotonic() < deadline, _not_on_main(repo, sha, home)
            time.sleep(0.5)
        note = _wait_any(s, "reviewed by @alice-codex", timeout=60)["content"]
        assert "published" in note
    finally:
        s.close()


@pytest.mark.since("0.7.12")  # the fix ships in 0.7.12; the nightly skips it on older PyPI
def test_a_held_fix_is_announced_once_and_the_gate_line_wakes_nobody(tmp_path_factory):
    """docs/plans/first-ten-minutes.md: a builder that doesn't name the reviewer (real
    Claude Code often doesn't). On 0.7.11 every later builder turn re-posted the held
    line, the reviewer's reply woke the builder again, and it looped until the chatter
    guard; the chat gate line also woke both agents to say hi."""
    home = tmp_path_factory.mktemp("gate-once") / "home"
    repo, bin_dir = _setup(home)
    codex = bin_dir / "codex"
    codex.write_text(codex.read_text().replace("OTHER = 'alice-opencode'", "OTHER = None"))
    s = PtySession(home, cwd=repo, extra_path=bin_dir)
    try:
        _first_run(s, home, repo, write_answer="y", gate="")
        gate = _wait_any(s, "Review gate on:", timeout=30)
        assert gate.get("message_type") == "status"
        ask = "@alice-codex fix the bug in calc.py"
        s.type(ask + ENTER)
        _wait_any(s, "awaiting @alice-opencode's review", timeout=120)
        time.sleep(25)  # long enough for the 0.7.11 loop to have run several laps
        texts = [(m.get("from_name"), m.get("content") or "") for m in _history(s)]
        held = [c for _, c in texts if "awaiting @alice-opencode's review" in c]
        assert len(held) == 1, texts
        before_ask = texts[:next(i for i, (_, c) in enumerate(texts) if c == ask)]
        assert not [w for w, _ in before_ask if w and w.startswith("alice-")], before_ask
    finally:
        s.close()
