"""The CLI says why a room refused you, not that your secret is wrong.

0.6.0 docs pass, run against a real relay: an observer's `quorus say` printed
"Not a member of #team, join first", and after `quorus invites --revoke` the
invitee's `quorus history` said "The relay rejected your credentials... re-run
`quorus init --secret -`" (they never had a secret) or "retry, relay is up".
`quorus invites` also didn't show which invites were read-only.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
from quorus_cli import cli, invites_cmd

from quorus.routes.room_auth import OBSERVER_DENIED

_REQ = httpx.Request("POST", "http://relay.test/rooms/team/messages")


def _refused(detail: str, status: int = 403) -> httpx.HTTPStatusError:
    resp = httpx.Response(status, json={"detail": detail}, request=_REQ)
    return httpx.HTTPStatusError("refused", request=_REQ, response=resp)


@pytest.fixture
def errors(monkeypatch):
    seen: list[tuple[str, str]] = []

    def record(msg, hint="", **_kw):
        seen.append((msg, hint))

    monkeypatch.setattr(cli._ui, "error", record)
    monkeypatch.setattr(cli._ui, "error_with_retry", lambda msg, **kw: seen.append((msg, "retry")))
    return seen


def test_observer_say_names_the_read_only_invite(monkeypatch, errors):
    async def say(room, message):
        raise _refused(OBSERVER_DENIED)

    monkeypatch.setattr(cli, "_say", say)
    with pytest.raises(SystemExit):
        cli._cmd_say(SimpleNamespace(room="team", message="hi", message_parts=None, stdin=False))
    (msg, hint), = errors
    assert "read-only" in msg and "member invite" in hint
    assert "Not a member" not in msg


def test_non_member_say_keeps_the_join_hint(monkeypatch, errors):
    async def say(room, message):
        raise _refused("Not a member of this room")

    monkeypatch.setattr(cli, "_say", say)
    with pytest.raises(SystemExit):
        cli._cmd_say(SimpleNamespace(room="team", message="hi", message_parts=None, stdin=False))
    assert errors == [("Not a member of #team", "join first: quorus join team")]


def test_revoked_invitee_is_told_about_the_room_not_their_secret(errors):
    cli._report_http_error(_refused("Must be a room member to access this resource").response)
    assert cli._explain_http_error(_refused("Must be a room member to access this resource")) == 3
    for msg, hint in errors:
        assert "not in that room" in msg and "revoked" in hint
        assert "--secret" not in hint and "retry" not in hint


def test_a_real_auth_failure_still_points_at_credentials(errors):
    assert cli._explain_http_error(_refused("Invalid token", status=401)) == 3
    assert "credentials" in errors[0][0]


def test_invites_list_marks_read_only_invites(monkeypatch):
    rows = [
        {"id": "inv_aaaaaaaaaaaa", "room": "team", "role": "observer", "issuer": "admin",
         "expires_at": time.time() + 3600, "max_uses": 1, "uses": 0, "revoked": 0},
        {"id": "inv_bbbbbbbbbbbb", "room": "team", "role": "member", "issuer": "admin",
         "expires_at": time.time() + 3600, "max_uses": 1, "uses": 0, "revoked": 0},
    ]
    monkeypatch.setattr(cli, "RELAY_URL", "http://relay.test")
    monkeypatch.setattr(cli, "_auth_headers", lambda: {})
    monkeypatch.setattr(invites_cmd.httpx, "get",
                        lambda *a, **kw: httpx.Response(200, json=rows, request=_REQ))
    printed: list[str] = []
    monkeypatch.setattr(cli._ui.console, "print", lambda s="", *a, **kw: printed.append(s))
    assert invites_cmd.cmd_invites(SimpleNamespace(revoke=None)) == 0
    observer, member = printed[0], printed[1]
    assert "read-only" in observer and "read-only" not in member
