"""0.6.0 stranger test of invites (docs/internal/stranger-invites-0.6.0.md).

Two clean HOMEs on the PyPI wheel: the wizard asked for a quorus:// link that
nothing makes any more, took "hello from carol" as a relay address and then told
an invitee to `quorus init --secret`; a member's `quorus share` said "check
RELAY_SECRET" and exited 0, as did `history` after a revoke; a released key said
"retry — relay is up"; and every invite read "by admin".
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
from quorus_cli import cli, invites_cmd
from quorus_tui import hub, welcome

_REQ = httpx.Request("POST", "http://relay.test/v1/join/mint")


@pytest.fixture
def errors(monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(cli._ui, "error", lambda msg, hint="", **_k: seen.append((msg, hint)))
    monkeypatch.setattr(cli._ui, "error_with_retry",
                        lambda msg, **_k: seen.append((msg, "retry")))
    return seen


def test_join_prompts_ask_only_for_the_code_strangers_get():
    # F1: `quorus share` prints an ABCD-EFGH code; nothing makes these tokens.
    labels = " ".join(f"{label} {desc}" for _key, label, desc in welcome._ACTIONS)
    assert "quorus_join_" not in labels and "token" not in labels
    import inspect
    wizard = inspect.getsource(hub)
    assert "quorus://…[/][muted] link" not in wizard
    assert "ABCD-EFGH code or quorus:// link" not in wizard


def test_wizard_asks_again_for_a_relay_address_that_isnt_one(monkeypatch):
    # F2: a stray message was "resolved" as the relay address.
    answers = iter(["hello from carol", "192.168.1.20", "http://192.168.1.20:8080/"])
    monkeypatch.setattr(hub.Prompt, "ask", lambda *_a, **_k: next(answers))
    printed: list[str] = []
    console = SimpleNamespace(print=lambda *a, **_k: printed.append(" ".join(map(str, a))))
    assert hub._ask_relay_address(console) == "http://192.168.1.20:8080"
    assert sum("isn't an address" in p for p in printed) == 2
    # Malformed, out-of-range ports and URL credentials: asked again, never echoed.
    printed.clear()
    tricky = iter(["http://[bad", "http://h:99999", "http://bob:pw@h:8080"])
    monkeypatch.setattr(hub.Prompt, "ask", lambda *_a, **_k: next(tricky))
    assert hub._ask_relay_address(console) == ""
    assert len(printed) == 3 and not any("pw" in p for p in printed)
    bad = iter(["nope"] * 3)
    monkeypatch.setattr(hub.Prompt, "ask", lambda *_a, **_k: next(bad))
    assert hub._ask_relay_address(console) == ""


def test_a_member_sharing_is_told_who_can_and_exits_non_zero(monkeypatch, errors):
    # F3: "admin-only — check RELAY_SECRET or API key role", exit 0.
    monkeypatch.setattr(cli, "RELAY_URL", "http://relay.test")
    monkeypatch.setattr(cli, "RELAY_SECRET", "")
    monkeypatch.setattr(cli, "API_KEY", "quorus_pk_member")
    monkeypatch.setattr(cli, "_auth_headers", lambda: {})
    monkeypatch.setattr(cli.httpx, "post", lambda *a, **k: httpx.Response(
        403, json={"detail": "admin only"}, request=_REQ))
    with pytest.raises(SystemExit) as exit_:
        cli._cmd_share(SimpleNamespace(room="general", ttl=1, uses=1, copy=False))
    assert exit_.value.code == 3
    (msg, hint), = errors
    assert "owner" in msg and "RELAY_SECRET" not in hint and "admin" not in hint


def test_a_refused_read_exits_non_zero(errors):
    # F4: `quorus history` on a room you were removed from exited 0.
    resp = httpx.Response(403, json={"detail": "Must be a room member to access this resource"},
                          request=_REQ)
    with pytest.raises(SystemExit) as exit_:
        cli._report_http_error(resp)
    assert exit_.value.code == 3


def test_a_released_key_is_not_told_to_retry(monkeypatch, errors):
    # F6: "HTTP 401: Invalid participant key · hint: retry — relay is up".
    resp = httpx.Response(401, json={"detail": "Invalid participant key"}, request=_REQ)
    with pytest.raises(SystemExit) as exit_:
        cli._report_http_error(resp)
    assert exit_.value.code == 3
    (msg, hint), = errors
    assert "no longer works" in msg and "new invite" in hint and hint != "retry"

    async def say(room, message):
        raise httpx.HTTPStatusError("x", request=_REQ, response=resp)

    errors.clear()
    monkeypatch.setattr(cli, "_say", say)
    with pytest.raises(SystemExit):
        cli._cmd_say(SimpleNamespace(room="general", message="hi", message_parts=None,
                                     stdin=False))
    (msg, hint), = errors
    assert "no longer works" in msg and "Send failed" not in msg


def test_the_owner_sees_their_own_invites_not_admins(monkeypatch):
    # F5: every invite read "by admin".
    rows = [{"id": "inv_aaaaaaaaaaaa", "room": "general", "role": "member", "issuer": "admin",
             "expires_at": time.time() + 3600, "max_uses": 1, "uses": 0, "revoked": 0},
            {"id": "inv_bbbbbbbbbbbb", "room": "general", "role": "member", "issuer": "bob",
             "expires_at": time.time() + 3600, "max_uses": 1, "uses": 0, "revoked": 0}]
    monkeypatch.setattr(cli, "RELAY_URL", "http://relay.test")
    monkeypatch.setattr(cli, "_auth_headers", lambda: {})
    monkeypatch.setattr(invites_cmd.httpx, "get",
                        lambda *a, **kw: httpx.Response(200, json=rows, request=_REQ))
    printed: list[str] = []
    monkeypatch.setattr(cli._ui.console, "print", lambda s="", *a, **kw: printed.append(s))
    assert invites_cmd.cmd_invites(SimpleNamespace(revoke=None)) == 0
    assert "by a relay-secret holder" in printed[0] and "by bob" in printed[1]


def test_release_name_says_the_name_stays_reserved(tmp_path, capsys):
    # F7: "the next claim gets a new key" read as "the name is free again", but
    # names carry rooms, creator rights and memory; reuse stays refused.
    from quorus_cli.bundle_cmd import cmd_release_name
    from rich.console import Console

    from quorus.auth.participant_keys import ParticipantKeyStore

    ParticipantKeyStore(tmp_path).claim("bob")
    console = Console(force_terminal=False, width=200)
    cmd_release_name(SimpleNamespace(name="bob", data_dir=str(tmp_path)), console)
    out = capsys.readouterr().out
    assert "stays reserved" in out and "new name" in out
    assert "next claim gets a new key" not in out
