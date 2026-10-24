"""TUI status row (tui-polish.md §3.3): its eight states, their precedence and the
lines that never count as an answer."""
from datetime import datetime

import pytest
from quorus_tui.onboarding import room_state

NOW = 1_800_000_000.0
ROOM = "general"
MEMBERS = ["sam", "sam-opencode"]
FREE_BUSY = ("⚠ opencode's free tier has hit its limit for this connection or account, "
             "so no free model can answer right now.")


def msg(who, body, kind="chat", ts=""):
    return {"from_name": who, "content": body, "message_type": kind, "timestamp": ts}


def online(name="sam-opencode", status="active", room=ROOM, activity=""):
    return {"name": name, "online": True, "status": status, "room": room,
            "activity": activity}


def state(messages=(), presence=None, members=MEMBERS, connected=True, **kw):
    presence = [online()] if presence is None else presence
    return room_state(connected, list(members), presence, list(messages), NOW, room=ROOM, **kw)


def test_one_case_per_state():
    ask, reply = msg("sam", "why does add fail?"), msg("sam-opencode", "add() subtracts.")
    assert state(connected=False)[0] == "disconnected"
    assert state(members=["sam"]) == (
        "no agents", "No agents here yet · /agents to add one · /share to invite a person")
    assert state(presence=[]) == (
        "not running", "@sam-opencode isn't running · /agents to restart it")
    assert state() == ("ready", "@sam-opencode is here · say something to wake it")
    busy = [online(status="busy", activity="editing relay.py")]
    assert state([ask], presence=busy, busy_since={"sam-opencode": NOW - 12}) == (
        "working", "⋯ @sam-opencode editing relay.py 12s")
    assert state([ask, msg("sam-opencode", FREE_BUSY)])[0] == "free tier limited"
    assert state([ask])[0] == "waiting"
    assert state([ask, reply]) == ("answered", "@ mention · / commands · ^D quit")


def test_disconnected_wins_over_a_busy_agent():
    busy = [online(status="busy")]
    assert state([msg("sam", "hi")], presence=busy, connected=False)[0] == "disconnected"


def test_membership_alone_is_not_ready():
    offline = [{**online(), "online": False, "status": "offline"}]
    assert state(presence=offline)[0] == "not running"
    assert state(presence=[online(name="someone-else-codex")])[0] == "not running"
    assert state(presence=[])[0] == "not running"


def test_busy_in_another_room_is_not_working_here():
    assert state([msg("sam", "hi")], presence=[online(status="busy", room="other")])[0] == (
        "waiting")


@pytest.mark.parametrize("body,kind", [
    ("⚠ vendor usage limit reached", "chat"), ("❌ failed", "chat"),
    ("🔐 approval needed", "chat"), ("[reflexd] restarting", "chat"),
    ("(quorus) published 1 commit(s)", "chat"), ("plan: looking now", "chat"),
    ("NO_REPLY", "chat"), ("@sam-opencode is out of quota for about 2h", "chat"),
    ("@sam-opencode is paused for about 5 min", "status"),
])
def test_notices_after_a_question_leave_it_waiting(body, kind):
    assert state([msg("sam", "fix it"), msg("sam-opencode", body, kind)])[0] == "waiting"


def test_limit_notice_then_real_answer():
    ask = msg("sam", "fix it")
    limited = [ask, msg("sam-opencode", FREE_BUSY)]
    assert state(limited)[0] == "free tier limited"
    assert state(limited + [msg("sam-opencode", "Fixed in abc123.")])[0] == "answered"
    assert state(limited + [msg("sam", "still there?")])[0] == "waiting"


def test_waiting_counts_up_and_hints_after_two_minutes():
    ts = "2027-01-15T08:00:00+00:00"
    then = datetime.fromisoformat(ts).timestamp()
    ask = msg("sam", "fix it", ts=ts)
    soon = room_state(True, MEMBERS, [online()], [ask], then + 40, room=ROOM)
    assert soon == ("waiting", "@sam-opencode hasn't picked this up yet · 40s")
    late = room_state(True, MEMBERS, [online()], [ask], then + 125, room=ROOM)
    assert late[1].endswith("· 2m 05s · /agents to check it")


def test_order_not_timestamps_decides_answered():
    ask = msg("sam", "fix it", ts="2027-01-15T08:00:05+00:00")
    reply = msg("sam-opencode", "done", ts="2027-01-15T08:00:05+00:00")
    assert state([ask, reply])[0] == "answered"
    assert state([reply, ask])[0] == "waiting"


class _State:
    def __init__(self, presence):
        from quorus_tui.working import WorkingTracker
        self.working = WorkingTracker()
        if presence is not None:
            self.working.set_presence(presence)


def test_hub_row_waits_for_presence_and_hides_once_answered():
    from quorus_tui import hub
    ask, reply = msg("sam", "fix it"), msg("sam-opencode", "done")
    row = hub._room_state_row
    assert row(_State(None), True, MEMBERS, [ask], ROOM) is None  # no poll yet: no claim
    assert "Not connected" in row(_State(None), False, MEMBERS, [ask], ROOM).plain
    assert "hasn't picked this up" in row(_State([online()]), True, MEMBERS, [ask], ROOM).plain
    assert "isn't running" in row(_State([]), True, MEMBERS, [ask], ROOM).plain
    assert row(_State([online()]), True, MEMBERS, [ask, reply], ROOM) is None


def test_presence_loop_keeps_rows_for_the_status_row(monkeypatch):
    import threading

    from quorus_tui import working
    rows = [online()]
    monkeypatch.setattr(working, "fetch_presence", lambda relay, headers: rows)
    stop, tracker = threading.Event(), working.WorkingTracker()
    assert tracker.presence() is None
    monkeypatch.setattr(stop, "wait", lambda _s: stop.set())
    working.presence_loop("http://r", {}, tracker, lambda: ROOM, lambda _n: None, stop)
    assert tracker.presence() == rows
