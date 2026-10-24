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
    assert "Agent answered" in status(members, [answer], connected=True)
    assert "waiting" in status(members, [answer, msg("alice", "fix it")], connected=True)
    assert "waiting" in status(members, [answer, msg("alice-codex", "⚠ unavailable", "status")],
                               connected=True)
    assert "disconnected" in status(members, [answer], connected=False)
    assert "No agents connected" in status(["alice"], [answer], connected=True)


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


@pytest.mark.parametrize("first_run", [True, False])
def test_room_entry_never_discovers_or_starts_agents(monkeypatch, tmp_path, first_run):
    from quorus_cli import quickstart

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(quickstart, "offer", lambda *a, **k: pytest.fail("implicit setup"))
    monkeypatch.setattr(quickstart, "detect", lambda *a, **k: pytest.fail("implicit discovery"))
    state = hub.HubState()
    state.request_workspace_switch("personal")
    profile = {"relay_url": "http://127.0.0.1:8080", "instance_name": "alice",
               "chat_identity": "alice", "relay_secret": "test", "room": "general"}
    with patch.object(hub, "_get_auth_token", return_value="jwt"), \
         patch.object(hub, "_fetch_rooms", return_value=[{"name": "general"}]), \
         patch.object(hub, "_away_rows", return_value=[]), \
         patch.object(hub, "_poll_loop"), patch.object(hub, "_sse_loop"), \
         patch.object(hub, "_join_room"), patch.object(hub, "_load_history_into"):
        hub._run_session(profile, state=state, announce_ready=first_run)
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
