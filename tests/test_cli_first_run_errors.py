"""First-run error-message regressions (weekend brief, 2026-10-17).

A user who mistypes the relay host, hits a port with nothing on it, or runs
against a URL with no scheme used to get the same advice every time: "start it
with quorus relay". These tests pin the cause-specific messages.
"""

from __future__ import annotations

import socket
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest
import quorus_cli.cli as cli


def _flat(capsys) -> str:
    """Collapse the console's hard line wraps so substrings survive."""
    return " ".join(capsys.readouterr().out.split())


def _gaierror_connect_error() -> httpx.ConnectError:
    """What httpx raises for a host that does not resolve."""
    exc = httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")
    exc.__cause__ = socket.gaierror(8, "nodename nor servname provided, or not known")
    return exc


def _refused_connect_error() -> httpx.ConnectError:
    exc = httpx.ConnectError("[Errno 61] Connection refused")
    exc.__cause__ = ConnectionRefusedError(61, "Connection refused")
    return exc


@pytest.mark.parametrize("exc,url,reason_needle,fix_needle", [
    (_gaierror_connect_error(), "https://relay.exmaple.com",
     "could not resolve host 'relay.exmaple.com'", "typo"),
    (_refused_connect_error(), "http://127.0.0.1:8080",
     "nothing is listening at 127.0.0.1:8080", "quorus relay"),
    # async httpx drops the errno and reports "All connection attempts failed",
    # so the loopback heuristic has to carry the message.
    (httpx.ConnectError("All connection attempts failed"), "http://localhost:8080",
     "nothing is listening at localhost:8080", "quorus relay"),
    (httpx.ConnectTimeout("timed out"), "http://10.255.255.1:8080",
     "timed out connecting to 10.255.255.1:8080", "reachable"),
    (httpx.UnsupportedProtocol(
        "Request URL is missing an 'http://' or 'https://' protocol."), "relay.example.com",
     "no http:// or https:// scheme", "e.g. http://localhost:8080"),
])
def test_connect_failures_get_a_cause_specific_message(
    exc, url, reason_needle, fix_needle,
) -> None:
    reason, fix = cli._relay_error_reason(exc, url)
    assert reason_needle in reason
    assert fix_needle in fix


def test_non_connection_failures_keep_the_callers_wording() -> None:
    """An HTTP 500 is not an unreachable relay — don't claim it is."""
    req = httpx.Request("GET", "http://relay/x")
    resp = httpx.Response(500, request=req)
    status = httpx.HTTPStatusError("boom", request=req, response=resp)
    assert cli._relay_error_reason(status, "http://relay") == ("", "")


def test_relay_unreachable_names_the_dns_failure(capsys) -> None:
    cli._relay_unreachable("https://relay.exmaple.com", exc=_gaierror_connect_error())
    out = _flat(capsys)
    assert "Cannot connect to relay at https://relay.exmaple.com" in out
    assert "could not resolve host 'relay.exmaple.com'" in out
    assert "typo" in out


def test_main_surfaces_the_reason_and_keeps_exit_code_2(monkeypatch, capsys) -> None:
    """Every unhandled connection failure goes through main()'s handler."""

    def boom(args):
        raise _gaierror_connect_error()

    monkeypatch.setitem(cli.__dict__, "_cmd_rooms", boom)
    monkeypatch.setattr(cli, "RELAY_URL", "https://relay.exmaple.com")
    monkeypatch.setattr(sys, "argv", ["quorus", "rooms"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    out = _flat(capsys)
    assert "Can't reach the relay" in out
    assert "relay.exmaple.com" in out and "typo" in out


# ── doctor ──────────────────────────────────────────────────────────────


def _doctor_args() -> MagicMock:
    args = MagicMock()
    args.verbose = False
    return args


def test_doctor_explains_dns_failure(capsys, tmp_path) -> None:
    with patch("httpx.get", side_effect=_gaierror_connect_error()), \
         patch("quorus_cli.cli.RELAY_URL", "https://relay.exmaple.com"), \
         patch("quorus.config.resolve_config_file") as resolve, \
         patch("quorus_cli.cli.Path.home", return_value=tmp_path), \
         patch("quorus_cli.cli.Path.cwd", return_value=tmp_path):
        resolve.return_value.exists.return_value = True
        with pytest.raises(SystemExit):
            cli._cmd_doctor(_doctor_args())

    out = _flat(capsys)
    assert "Relay reachable" in out
    assert "could not resolve host" in out
    # the typo fix, not the "start the relay" advice
    assert "typo" in out


def test_doctor_reports_rejected_credentials_with_status(capsys, tmp_path) -> None:
    health = MagicMock(status_code=200)
    rooms = MagicMock(status_code=401)

    def fake_get(url, **kwargs):
        return health if "health" in url else rooms

    with patch("httpx.get", side_effect=fake_get), \
         patch("quorus_cli.cli.RELAY_SECRET", "wrong"), \
         patch("quorus_cli.cli.INSTANCE_NAME", "alice"), \
         patch("quorus.config.resolve_config_file") as resolve, \
         patch("quorus_cli.cli.Path.home", return_value=tmp_path), \
         patch("quorus_cli.cli.Path.cwd", return_value=tmp_path):
        resolve.return_value.exists.return_value = True
        with pytest.raises(SystemExit):
            cli._cmd_doctor(_doctor_args())

    out = _flat(capsys)
    assert "rejected these credentials (HTTP 401)" in out
    assert "relay_secret" in out
