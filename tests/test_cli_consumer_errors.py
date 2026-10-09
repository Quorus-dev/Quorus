"""Consumer-navigation regressions from the 2026-10-09 CLI sweep."""

from __future__ import annotations

from typing import Any

import httpx
import pytest


def _status_error(code: int, detail: Any) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://relay/x")
    resp = httpx.Response(code, json={"detail": detail}, request=req)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


@pytest.mark.parametrize("code,detail,exit_code,needle", [
    (401, "Invalid or missing auth token", 3, "rejected your credentials"),
    (404, "Participant not in room", 4, "Participant not in room"),
    (422, [{"msg": "only alphanumeric characters, hyphens, and underscores"}], 5,
     "only alphanumeric"),
    (500, "kaboom", 1, "HTTP 500"),
])
def test_relay_errors_become_one_readable_line(code, detail, exit_code, needle, capsys) -> None:
    import quorus_cli.cli as cli

    assert cli._explain_http_error(_status_error(code, detail)) == exit_code
    out = capsys.readouterr().out
    assert needle in out and "Traceback" not in out and "{'" not in out


def test_missing_room_is_a_clean_error_not_a_traceback(monkeypatch, capsys) -> None:
    import sys

    import quorus_cli.cli as cli

    async def fake_say(room, msg):
        raise cli._RoomNotFound(f"Room '{room}' not found")

    monkeypatch.setattr(cli, "_say", fake_say)
    monkeypatch.setattr(sys, "argv", ["quorus", "say", "nosuchroom", "hi"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 4
    assert "not found" in capsys.readouterr().out


def test_relay_down_is_a_clean_error(monkeypatch, capsys) -> None:
    import sys

    import quorus_cli.cli as cli

    def fake_rooms(args):
        raise httpx.ConnectError("refused")

    monkeypatch.setitem(cli.__dict__, "_cmd_rooms", fake_rooms)
    monkeypatch.setattr(sys, "argv", ["quorus", "members", "x"])
    monkeypatch.setattr(cli, "_cmd_members", fake_rooms, raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "Can't reach the relay" in capsys.readouterr().out


def test_local_relay_never_hijacks_a_relay_with_another_secret(monkeypatch) -> None:
    from quorus.runtime import local_relay as lr

    monkeypatch.setattr(lr, "_healthy", lambda url, timeout=2.0: True)
    monkeypatch.setattr(lr, "_accepts", lambda url, secret: False)
    started: list[Any] = []
    monkeypatch.setattr(lr.subprocess, "Popen", lambda *a, **k: started.append(a))
    ok, note = lr.ensure_local_relay("http://localhost:8080", "mine")
    assert not ok and "different secret" in note and not started
    assert lr.is_local("http://127.0.0.1:9") and not lr.is_local("https://relay.example.com")


def test_agent_list_shows_only_my_agents(monkeypatch, capsys) -> None:
    from quorus_cli import agent_cmd

    monkeypatch.setattr(agent_cmd, "load_config", lambda: {"instance_name": "arav"})
    monkeypatch.setattr(agent_cmd, "_launchd_labels", lambda: [
        "dev.quorus.agent.arav-claude", "dev.quorus.agent.bob-claude",
        "dev.quorus.dogfood.reflexd.arav-codex", "com.other.thing"])

    class C:
        def print(self, *a: Any, **k: Any) -> None:
            print(*a)

    agent_cmd._list(C())
    out = capsys.readouterr().out
    assert "arav-claude" in out and "arav-codex" in out and "bob-claude" not in out
