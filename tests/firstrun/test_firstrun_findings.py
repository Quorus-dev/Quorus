"""New-user pass findings, pinned: plain `quorus`, a clean HOME, a real pty.

Each test replays what a manual new-user pass caught.
Anything the user types is checked in the local relay's own room history, not
on screen; the screen is checked only for what is purely visible. One first
run is shared by the module; each test then sends its own uniquely marked
message, so no test can pass on another's output.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

import quorus

# Skip before importing the harness: it needs fcntl/termios, which Windows lacks.
pytest.importorskip("fcntl", reason="needs a POSIX pty")
from tests.firstrun.pty_session import ENTER, ESC, PtySession, plain

pytestmark = pytest.mark.firstrun


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    s = PtySession(tmp_path_factory.mktemp("firstrun") / "home")
    try:
        s.welcome = s.expect(r"Your name", timeout=30)
        s.type("alice" + ENTER)
        s.expect(r"Invite, or Enter to run it here")
        s.type(ENTER)
        # The session's own line, whichever screen it lands on: F1 is about which.
        s.expect(r"You're in as @alice", timeout=45)
        s.settle(quiet=1.0, timeout=10)
        s.landed = plain(s.text()[s._mark:])
        s.secret = json.loads((s.home / ".quorus" / "config.json").read_text())["relay_secret"]
        yield s
    finally:
        s.close()


def _history(s: PtySession) -> list[dict]:
    with httpx.Client(trust_env=False, timeout=5) as c:
        r = c.get(f"{s.relay_url}/rooms/general/history", params={"limit": 100},
                  headers={"Authorization": f"Bearer {s.secret}"})
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("messages", [])


def _sent(s: PtySession, marker: str, timeout: float = 8.0) -> list[str]:
    """What alice's messages containing *marker* say, once one has landed."""
    deadline = time.monotonic() + timeout
    while True:
        got = [m["content"] for m in _history(s)
               if m.get("from_name") == "alice" and marker in (m.get("content") or "")]
        if got or time.monotonic() > deadline:
            return got
        time.sleep(0.2)


def test_f2_the_banner_shows_the_installed_version(session) -> None:
    """F2 (fixed in db6ab005): the banner said v0.5.0 whatever was installed."""
    assert f"v{quorus.__version__}" in session.welcome


def test_f1_the_first_message_lands_in_general(session) -> None:
    """F1 (fixed in db6ab005): the first run ended on the hub home with no room
    open, so the first thing a new user typed went nowhere."""
    assert "Type a message" in session.landed and "#general" in session.landed
    session.type("loopc-f1 hello" + ENTER)
    assert _sent(session, "loopc-f1") == ["loopc-f1 hello"]


def test_f3_a_multi_line_paste_arrives_whole(session) -> None:
    """F3 (fixed in af7d80ea): the composer read stdin through Python's buffer,
    so a paste sat unsent until another key was pressed."""
    session.paste("loopc-f3 first line\nsecond line" + ENTER)
    assert _sent(session, "loopc-f3") == ["loopc-f3 first line\nsecond line"]


def test_f4_a_pasted_mention_gets_past_the_picker(session) -> None:
    """F4 (fixed in af7d80ea): a pasted `@name ...` stuck in the mention picker."""
    session.paste("@alice loopc-f4 what does calc.py do?" + ENTER)
    assert _sent(session, "loopc-f4") == ["@alice loopc-f4 what does calc.py do?"]


def test_esc_on_the_slash_menu_drops_the_half_typed_command(session) -> None:
    """Pass 3 (fixed in 677b0b0c): Esc closed the / menu but kept "/h", so the
    next words went out as the unknown command "/h..."."""
    session.type("/h")
    session.type(ESC)
    time.sleep(0.3)
    session.type("loopc-esc after escape" + ENTER)
    assert _sent(session, "loopc-esc") == ["loopc-esc after escape"]


def test_an_exact_slash_command_runs_on_the_first_enter(session) -> None:
    """0.6.3 audit (HUB lane): `/rooms` + Enter only autocompleted to `/rooms `;
    the command ran on the second Enter, and `/quit` never quit."""
    session.type("/rooms" + ENTER)
    session.expect(r"Rooms|#general", timeout=8)
    time.sleep(0.5)
    session.type("loopc-one-enter after rooms" + ENTER)
    assert _sent(session, "loopc-one-enter") == ["loopc-one-enter after rooms"]


def test_the_first_run_starts_one_relay_on_its_own_url(session) -> None:
    """Found building loop C: the wizard set its relay URL but started the relay
    on the hard-coded default (8080); the hub then started a second one."""
    log = (session.home / ".quorus" / "relay.log").read_text()
    assert log.count("Uvicorn running on") == 1
    assert f"127.0.0.1:{session.port}" in log


def test_wait_for_backs_off_when_the_relay_says_429(monkeypatch, tmp_path):
    """Overseer, 0.6.3 wheel run: under a loaded suite wait_for's 0.5 s poll got 429 and
    the test died; it now sleeps for the relay's Retry-After and keeps waiting."""
    from tests.firstrun import stub_agents

    replies = [stub_agents.RelayBusy(3.0), stub_agents.RelayBusy(3.0),
               [{"from_name": "alice-codex", "content": "Hi: ready"}]]

    def fake_history(s, room="general"):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    sleeps: list[float] = []
    monkeypatch.setattr(stub_agents, "history", fake_history)
    monkeypatch.setattr(stub_agents.time, "sleep", sleeps.append)

    class S:
        home = tmp_path

    assert stub_agents.wait_for(S(), "alice-codex", "Hi:", timeout=60) == "Hi: ready"
    assert sleeps == [3.0, 3.0]
    assert stub_agents.retry_after("120") == 10.0  # capped
    assert stub_agents.retry_after(None) == 2.0 and stub_agents.retry_after("soon") == 2.0
