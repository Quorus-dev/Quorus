"""No-model entry and explicit setup preserve the real room and human identity."""
import io
from unittest.mock import patch

import pytest
from quorus_tui import hub, onboarding
from rich.console import Console


def msg(who, body, kind="chat"):
    return {"id": "m1", "from_name": who, "content": body, "message_type": kind}


def test_membership_and_notices_are_not_answers():
    members = ["alice", "alice-codex"]
    status = onboarding.room_status
    assert "No agents connected" in status(["alice"], [], connected=True)
    assert "waiting" in status(members, [], connected=True)
    for body, kind in [("joined", "join"), ("plan: looking", "chat"),
                       ("⚠ vendor usage limit reached", "chat"),
                       ("❌ failed", "chat"), ("🔐 approval needed", "chat"),
                       ("NO_REPLY", "chat"), ("done", "status")]:
        assert "waiting" in status(members, [msg("alice-codex", body, kind)], connected=True)
    answer = msg("alice-codex", "The test fails because add subtracts.")
    assert status(members, [answer], connected=True) is None
    assert status(members, [answer, msg("alice", "fix it")], connected=True) is None
    assert status(members, [answer, msg("alice-codex", "⚠ unavailable", "status")],
                  connected=True) is None
    assert status(members, [answer], connected=False) is None
    assert status(["alice"], [answer], connected=True) is None
    assert "disconnected" in status(members, [], connected=False)


def test_setup_can_be_canceled_and_retried_without_losing_the_room(monkeypatch):
    from quorus_cli import quickstart

    monkeypatch.setattr("quorus.config.load_config", lambda: {"api_key": "quorus_pk_test"})
    state = hub.HubState()
    state.set_rooms([{"name": "general", "members": ["alice"]}])
    state.select_by_name("general")
    state.set_connected(True, "Connected")
    state.messages = [msg("alice", "hello")]
    monkeypatch.setattr(hub, "_invited_elsewhere", lambda *a: True)
    calls = []

    def offer(console, room, **kw):
        calls.append((room, kw))
        if len(calls) == 1:
            raise KeyboardInterrupt
        return ["codex"]

    monkeypatch.setattr(quickstart, "offer", offer)
    console = Console(file=io.StringIO())
    for _ in range(2):
        assert hub._dispatch_slash("/agents", "", state, "http://relay", "jwt", "alice",
                                   console)
    assert calls == [("general", {"invitee": True})] * 2
    assert "waiting for a real response" in state.get_status_bar()
    assert state.get_selected_room()["name"] == "general"
    assert state.messages[0]["content"] == "hello"


@pytest.mark.parametrize("persona", ["returning", "owner", "invitee"])
@pytest.mark.parametrize("outcome", ["decline", "interrupt"])
def test_initial_offer_and_returning_room_setup(monkeypatch, tmp_path, persona, outcome):
    from quorus_cli import quickstart

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    offers = []

    def offer(console, room, **kw):
        offers.append((room, kw))
        if outcome == "interrupt":
            raise KeyboardInterrupt
        return []

    monkeypatch.setattr(quickstart, "offer", offer)
    monkeypatch.setattr(hub, "_invited_elsewhere", lambda *a: persona == "invitee")
    monkeypatch.setattr(quickstart, "detect", lambda *a, **k: pytest.fail("implicit discovery"))
    state = hub.HubState()
    state.request_workspace_switch("personal")
    profile = {"relay_url": "http://127.0.0.1:8080", "instance_name": "alice",
               "chat_identity": "alice", "relay_secret": "test", "room": "general"}
    room = {"name": "general"}
    fetches = [[], [room]] if persona == "owner" else [[room]]
    with patch.object(hub, "_get_auth_token", return_value="jwt"), \
         patch.object(hub, "_create_room", return_value=("ok", None)), \
         patch.object(hub, "_fetch_rooms", side_effect=fetches), \
         patch.object(hub, "_away_rows", return_value=[]), \
         patch.object(hub, "_poll_loop"), patch.object(hub, "_sse_loop"), \
         patch.object(hub, "_join_room"), patch.object(hub, "_load_history_into"):
        hub._run_session(profile, state=state, announce_ready=persona != "returning")
    assert offers == ([] if persona == "returning" else
                      [("general", {"invitee": True} if persona == "invitee" else {})])
    assert state.rooms == [room]
    assert "/agents" in hub.SLASH_COMMANDS


def test_setup_cancellation_keeps_the_composer_usable(monkeypatch):
    from quorus_cli import quickstart

    monkeypatch.setattr("quorus.config.load_config", lambda: {})
    monkeypatch.setattr(hub, "_invited_elsewhere", lambda *a: False)

    def cancel(*a, **kw):
        raise EOFError

    monkeypatch.setattr(quickstart, "offer", cancel)
    state = hub.HubState()
    state.set_rooms([{"name": "general", "members": ["alice"]}])
    state.select_by_name("general")
    state.set_connected(True, "Connected")
    out = io.StringIO()
    sent = []
    with patch.object(hub, "_read_input", side_effect=["/agents", "hello", "quit"]), \
         patch.object(hub, "_full_clear"), \
         patch.object(hub, "_send_message", side_effect=lambda *a: sent.append(a) or "m1"):
        hub._main_input_loop(console=Console(file=out, width=120), state=state,
                             relay_url="http://relay.test", secret="jwt", agent_name="alice")
    assert len(sent) == 1 and sent[0][-2:] == ("alice", "hello")
    assert state.get_selected_room()["name"] == "general"
    assert "No agents connected" in out.getvalue()


def test_setup_failure_explains_the_cause(monkeypatch):
    from quorus_cli import quickstart

    monkeypatch.setattr("quorus.config.load_config", lambda: {})
    monkeypatch.setattr(hub, "_invited_elsewhere", lambda *a: False)

    def fail(*a, **kw):
        raise RuntimeError("CLI [unavailable]")

    monkeypatch.setattr(quickstart, "offer", fail)
    state = hub.HubState()
    state.set_rooms([{"name": "general"}])
    state.select_by_name("general")
    out = io.StringIO()
    onboarding.setup_agents("", state, "http://relay.test", "jwt", "alice",
                            Console(file=out, width=160))
    assert "CLI [unavailable]" in out.getvalue()
    assert state.get_selected_room()["name"] == "general"
    assert "/agents to retry" in state.get_status_bar()
