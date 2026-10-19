"""0.6.0 stranger pass: commands a new user tries on day 1 say plainly what they
do. `quorus web nosuchroom` opened an empty board and exited 0; `track-report`
and `tune` spoke of "loop A", "bids" and "track logs" before any were possible.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from quorus_cli import web_cmd

_REQ = httpx.Request("GET", "http://relay.test/rooms")
_JARGON = ("loop A", "bid ", "bids", "track log", "matched turns", "first free agent")


def _rooms(monkeypatch, rooms):
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: httpx.Response(200, json=rooms, request=_REQ))


def test_web_on_a_room_that_doesnt_exist_fails_and_lists_yours(monkeypatch, capsys):
    _rooms(monkeypatch, [{"name": "general"}, {"name": "build"}])
    with pytest.raises(SystemExit) as exit_:
        web_cmd._check_room("http://relay.test", "tok", "nosuchroom")
    assert exit_.value.code == 4
    err = capsys.readouterr().err
    assert "no room 'nosuchroom'" in err and "#build, #general" in err


def test_web_on_a_real_room_or_an_unreachable_relay_goes_ahead(monkeypatch):
    _rooms(monkeypatch, [{"name": "general"}])
    web_cmd._check_room("http://relay.test", "tok", "#general")  # no exit

    def down(*_a, **_k):
        raise httpx.ConnectError("down", request=_REQ)

    monkeypatch.setattr(httpx, "get", down)
    web_cmd._check_room("http://relay.test", "tok", "nosuchroom")  # the page says the rest


def test_day1_track_report_and_tune_speak_plainly(tmp_path, monkeypatch):
    from quorus_cli import track_report_cmd, tune_cmd

    printed: list[str] = []
    console = SimpleNamespace(print=lambda s="", *a, **k: printed.append(str(s)))
    track_report_cmd.cmd_track_report(SimpleNamespace(runtime_dir=str(tmp_path), agent=None,
                                                      json=False), console)
    monkeypatch.setenv("QUORUS_TUNING_FILE", str(tmp_path / "tuning.json"))
    tune_cmd.cmd_tune(SimpleNamespace(action="list", id=None, json=False), console)
    text = " ".join(printed)
    assert "@open <task>" in text and "Nothing learned yet" in text
    assert not any(word in text for word in _JARGON)
