"""Tests for ``quorus heartbeat`` and the ``quorus s`` (say alias) command.

heartbeat implements QOD rule #4: an idle-while-working alive signal posted to
the agent's primary room. Behaviors pinned by these tests:

- Auto-resolves the primary room (most recently active) when --room is absent.
- Idempotent within a 30-second window (3 calls in 30s collapse to 1 message).
- --force bypasses the dedupe window.
- Persists last-status so subsequent calls reuse the cached status.
- ``quorus s`` is wired as a subcommand alias for ``quorus say``.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Same fixture pattern as tests/test_cli.py — pin RELAY_URL so any accidental
# network call would fail loud.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def configure_cli(monkeypatch, tmp_path):
    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://test-relay:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "test-secret")
    monkeypatch.setattr("quorus.cli.API_KEY", "")
    monkeypatch.setattr("quorus.cli._cached_jwt", None)
    monkeypatch.setattr("quorus.cli.INSTANCE_NAME", "test-user")
    # Sandbox the heartbeat dedupe state file under tmp_path so tests are
    # hermetic and don't read or write the user's real ~/.quorus dir.
    monkeypatch.setattr(
        "quorus.cli._heartbeat_state_path",
        lambda: tmp_path / "heartbeat_state.json",
    )


def _mock_response(status_code, json_data):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _mock_client_for_heartbeat(rooms_payload, post_payload=None):
    """Return an AsyncMock client whose .get returns rooms_payload and .post
    returns post_payload."""
    rooms_resp = _mock_response(200, rooms_payload)
    post_resp = _mock_response(200, post_payload or {"id": "m1"})
    client = AsyncMock()
    client.get = AsyncMock(return_value=rooms_resp)
    client.post = AsyncMock(return_value=post_resp)
    client.aclose = AsyncMock()
    return client, rooms_resp, post_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_resolve_primary_room_picks_most_recent():
    """The primary-room resolver should pick the room whose last_message_at is
    newest among the rooms visible to the caller."""
    from quorus.cli import _resolve_primary_room

    rooms = [
        {"id": "r1", "name": "old-room", "last_message_at": "2026-01-01T00:00:00Z"},
        {"id": "r2", "name": "fresh-room", "last_message_at": "2026-05-01T00:00:00Z"},
        {"id": "r3", "name": "mid-room", "last_message_at": "2026-04-01T00:00:00Z"},
    ]
    client, _, _ = _mock_client_for_heartbeat(rooms)
    with patch("quorus.cli._get_client", return_value=client):
        room = await _resolve_primary_room()
    assert room == "fresh-room"


async def test_resolve_primary_room_no_rooms_returns_none():
    from quorus.cli import _resolve_primary_room

    client, _, _ = _mock_client_for_heartbeat([])
    with patch("quorus.cli._get_client", return_value=client):
        room = await _resolve_primary_room()
    assert room is None


def test_heartbeat_command_posts_to_primary_room(monkeypatch):
    """Smoke: ``quorus heartbeat --status X`` resolves a primary room and posts."""
    from quorus.cli import _cmd_heartbeat

    rooms = [
        {"id": "r1", "name": "yc-hack", "last_message_at": "2026-05-01T00:00:00Z"},
    ]
    client, _, post_resp = _mock_client_for_heartbeat(rooms)
    with patch("quorus.cli._get_client", return_value=client):
        args = MagicMock()
        args.status = "wiring up SSE"
        args.room = None
        args.force = False
        _cmd_heartbeat(args)

    # Assert: at least one POST happened (the heartbeat send).
    assert client.post.await_count >= 1, "heartbeat must post a message"
    # And the body contains the heartbeat marker + the status text.
    last_call = client.post.call_args_list[-1]
    body = last_call.kwargs.get("json") or {}
    assert "still working on: wiring up SSE" in body.get("content", "")
    assert "\U0001f493" in body.get("content", "")


def test_heartbeat_idempotent_within_window(monkeypatch, tmp_path):
    """Second heartbeat with the same status inside the 30s window should be
    suppressed — no second POST."""
    from quorus.cli import _cmd_heartbeat

    rooms = [
        {"id": "r1", "name": "yc-hack", "last_message_at": "2026-05-01T00:00:00Z"},
    ]
    client, _, _ = _mock_client_for_heartbeat(rooms)
    with patch("quorus.cli._get_client", return_value=client):
        args = MagicMock()
        args.status = "writing tests"
        args.room = None
        args.force = False
        _cmd_heartbeat(args)
        first_calls = client.post.await_count

        # Immediately fire again with the same status — should be deduped.
        args2 = MagicMock()
        args2.status = "writing tests"
        args2.room = None
        args2.force = False
        _cmd_heartbeat(args2)
        second_calls = client.post.await_count

    assert first_calls == 1, "first heartbeat must post exactly once"
    assert second_calls == 1, (
        f"duplicate heartbeat within window must be suppressed, got {second_calls} POSTs"
    )


def test_heartbeat_force_bypasses_dedupe(monkeypatch):
    """--force should send even within the dedupe window."""
    from quorus.cli import _cmd_heartbeat

    rooms = [
        {"id": "r1", "name": "yc-hack", "last_message_at": "2026-05-01T00:00:00Z"},
    ]
    client, _, _ = _mock_client_for_heartbeat(rooms)
    with patch("quorus.cli._get_client", return_value=client):
        args = MagicMock()
        args.status = "writing tests"
        args.room = None
        args.force = False
        _cmd_heartbeat(args)
        # Force a second send.
        args2 = MagicMock()
        args2.status = "writing tests"
        args2.room = None
        args2.force = True
        _cmd_heartbeat(args2)

    assert client.post.await_count == 2, "force must bypass dedupe"


def test_heartbeat_three_calls_in_30s_collapses_to_one(monkeypatch):
    """Spec acceptance: 3 calls within 30 seconds → 1 message."""
    from quorus.cli import _cmd_heartbeat

    rooms = [{"id": "r1", "name": "yc-hack", "last_message_at": "2026-05-01T00:00:00Z"}]
    client, _, _ = _mock_client_for_heartbeat(rooms)
    with patch("quorus.cli._get_client", return_value=client):
        for _ in range(3):
            args = MagicMock()
            args.status = "writing tests"
            args.room = None
            args.force = False
            _cmd_heartbeat(args)

    assert client.post.await_count == 1, (
        f"3 heartbeat calls in 30s must collapse to 1 POST, got {client.post.await_count}"
    )


def test_heartbeat_changed_status_sends_new_message(monkeypatch):
    """Even within the dedupe window, a new status text should send."""
    from quorus.cli import _cmd_heartbeat

    rooms = [{"id": "r1", "name": "yc-hack", "last_message_at": "2026-05-01T00:00:00Z"}]
    client, _, _ = _mock_client_for_heartbeat(rooms)
    with patch("quorus.cli._get_client", return_value=client):
        args1 = MagicMock()
        args1.status = "writing tests"
        args1.room = None
        args1.force = False
        _cmd_heartbeat(args1)

        args2 = MagicMock()
        args2.status = "fixing tests"
        args2.room = None
        args2.force = False
        _cmd_heartbeat(args2)

    assert client.post.await_count == 2


def test_heartbeat_persists_status_for_next_call(monkeypatch, tmp_path):
    """A bare ``quorus heartbeat`` (no --status) reuses the last cached status."""
    from quorus.cli import _cmd_heartbeat, _heartbeat_state_path

    rooms = [{"id": "r1", "name": "yc-hack", "last_message_at": "2026-05-01T00:00:00Z"}]
    client, _, _ = _mock_client_for_heartbeat(rooms)
    with patch("quorus.cli._get_client", return_value=client):
        # Seed with an explicit status.
        args1 = MagicMock()
        args1.status = "running migrations"
        args1.room = None
        args1.force = False
        _cmd_heartbeat(args1)

    state = json.loads(_heartbeat_state_path().read_text())
    assert state["last_status"] == "running migrations"
    assert state["last_room"] == "yc-hack"


def test_heartbeat_no_rooms_exits_nonzero(monkeypatch):
    """If the user has no rooms, heartbeat must surface an error."""
    from quorus.cli import _cmd_heartbeat

    client, _, _ = _mock_client_for_heartbeat([])
    with patch("quorus.cli._get_client", return_value=client):
        args = MagicMock()
        args.status = "x"
        args.room = None
        args.force = False
        with pytest.raises(SystemExit) as exc:
            _cmd_heartbeat(args)
    # exit code 4 = not found (per cli.py exit-code map)
    assert exc.value.code == 4


def test_heartbeat_explicit_room_skips_resolution(monkeypatch):
    """--room <name> should bypass the resolver and post directly to that room."""
    from quorus.cli import _cmd_heartbeat

    # Note: still need a rooms response for the underlying _say() call, which
    # looks up the room by name. Provide it.
    rooms = [
        {"id": "r1", "name": "explicit-room", "members": ["test-user"]},
    ]
    client, _, _ = _mock_client_for_heartbeat(rooms)
    with patch("quorus.cli._get_client", return_value=client):
        args = MagicMock()
        args.status = "x"
        args.room = "explicit-room"
        args.force = False
        _cmd_heartbeat(args)

    assert client.post.await_count == 1


# ---------------------------------------------------------------------------
# `quorus s` — say alias
# ---------------------------------------------------------------------------


def test_say_alias_s_in_dispatch_table():
    """The dispatch table must wire 's' to the same handler as 'say'.

    Smoke check via reading the cli module's source — argparse aliases work
    one way, dispatch tables work the other; both must agree.
    """
    from quorus import cli as cli_mod

    src = open(cli_mod.__file__).read()
    # The dispatch dict literally has both "say" and "s".
    assert '"s": _cmd_say' in src, "dispatch table must alias `s` to `_cmd_say`"
    # And argparse declares the alias.
    assert 'aliases=["s"]' in src, "argparse must declare 's' as an alias for 'say'"
