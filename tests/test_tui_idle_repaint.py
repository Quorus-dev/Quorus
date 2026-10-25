"""tui-polish.md defect 1: the legacy room cleared and reprinted the whole screen
on every 10 s tick, even with nothing new. An idle tick now repaints only when the
frame changed; new messages and user actions still repaint."""
import io
from unittest.mock import patch

from quorus_tui import hub
from rich.console import Console


def _state(messages=()):
    state = hub.HubState()
    state.set_rooms([{"name": "general", "members": ["alice", "alice-codex"]}])
    state.select_by_name("general")
    state.set_connected(True, "Connected")
    state.set_messages(list(messages))
    return state


def _run(state, inputs, monkeypatch):
    """Drive the real loop; count screen clears and keep the output."""
    clears = []
    monkeypatch.setattr(hub, "_full_clear", lambda console: clears.append(1))
    out = io.StringIO()
    with patch.object(hub, "_read_input", side_effect=inputs):
        hub._main_input_loop(console=Console(file=out, width=100), state=state,
                             relay_url="http://relay.test", secret="jwt",
                             agent_name="alice")
    return len(clears), out.getvalue()


def _msg(i):
    return {"id": f"m{i}", "from_name": "alice-codex", "content": f"answer {i}",
            "timestamp": "2027-01-15T08:00:00+00:00"}


def test_idle_ticks_with_nothing_new_do_not_repaint(monkeypatch):
    t = hub._KEY_TIMEOUT
    clears, out = _run(_state([_msg(0)]), [t, t, t, "quit"], monkeypatch)
    # One clear before the loop, one for the first frame; three idle ticks add none.
    assert clears == 2
    assert out.count("answer 0") == 1


def test_a_new_message_still_repaints_on_the_next_tick(monkeypatch):
    state = _state([_msg(0)])
    t = hub._KEY_TIMEOUT

    def inputs():
        yield t                          # idle: nothing new
        state.append_message(_msg(1))    # the SSE thread delivers a reply
        yield t                          # this tick must show it
        yield "quit"

    clears, out = _run(state, inputs(), monkeypatch)
    assert clears == 3 and "answer 1" in out


def test_latency_jitter_alone_is_not_a_change(monkeypatch):
    state = _state([_msg(0)])
    t = hub._KEY_TIMEOUT

    def inputs():
        for ms in (12, 47, 9):
            state.set_latency_ms(ms)
            yield t
        yield "quit"

    clears, _ = _run(state, inputs(), monkeypatch)
    assert clears == 2
