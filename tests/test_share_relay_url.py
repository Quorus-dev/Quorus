"""A tunnel address changes the invite output, never the authenticated mint target."""
from __future__ import annotations

import argparse
import shlex
from unittest.mock import Mock

import httpx
import pytest
from quorus_cli import cli, ui
from quorus_cli.share_url import public_relay_url

LOCAL = "http://127.0.0.1:8787"
PUBLIC = "https://quick-tunnel.trycloudflare.com"
CODE = "ABCD-EFGH"


@pytest.fixture
def mint(monkeypatch):
    monkeypatch.setattr(cli, "RELAY_URL", LOCAL)
    monkeypatch.setattr(cli, "RELAY_SECRET", "synthetic-secret")
    monkeypatch.setattr(cli, "API_KEY", "")
    monkeypatch.setattr(cli, "_admin_headers", lambda: {"Authorization": "Bearer synthetic-secret"})
    response = httpx.Response(200, request=httpx.Request("POST", LOCAL + "/v1/join/mint"),
                             json={"code": CODE,
                                   "install_url": LOCAL + f"/v1/join/install/{CODE}.sh"})
    post = Mock(return_value=response)
    monkeypatch.setattr(cli.httpx, "post", post)
    monkeypatch.setattr(ui.console, "width", 400)
    return post


def args(**kwargs):
    return argparse.Namespace(room="applications", ttl=1, uses=1, copy=False,
                              observer=False, **kwargs)


def test_flag_changes_join_and_install_but_mints_locally(mint, capsys):
    cli._cmd_share(args(relay_url=PUBLIC))
    shown = capsys.readouterr().out
    mint.assert_called_once()
    assert mint.call_args.args[0] == LOCAL + "/v1/join/mint"
    assert mint.call_args.kwargs["headers"] == {"Authorization": "Bearer synthetic-secret"}
    assert mint.call_args.kwargs["json"] == {
        "room": "applications", "ttl_days": 1, "max_uses": 1, "role": "member"}
    lines = [line.strip() for line in shown.splitlines()]
    join = shlex.split(next(line for line in lines if line.startswith("quorus join ")))
    install = shlex.split(next(line for line in lines if line.startswith("curl ")))
    assert join[join.index("--relay") + 1] == PUBLIC
    assert install[:3] == ["curl", "-sSL", PUBLIC + f"/v1/join/install/{CODE}.sh"]
    assert LOCAL not in shown and "synthetic-secret" not in shown


@pytest.mark.parametrize("url", [
    "ftp://relay.test", "https://relay.test//", "https://relay.test/path",
    "https://relay.test?token=synthetic", "https://relay.test?", "https://relay.test#x",
    "https://user:synthetic-password@relay.test", "https://user@relay.test",
    "https://relay.test:99999", "https://relay.test:", "https://relay.test:0", "",
    "http://localhost:8787", "https://LOCALHOST.", "http://sub.localhost",
    "http://127.0.0.1:8787", "http://127.2.3.4", "http://127.1", "http://2130706433",
    "http://0x7f000001", "http://[::1]:8787", "http://[::ffff:127.0.0.1]",
    "http://0.0.0.0", "http://[::]", " https://relay.test", "https://relay.test\n",
    "https://relay.test\\path", "https://%6cocalhost", "https://$(id).test",
])
def test_invalid_advertised_address_is_rejected_before_mint(mint, capsys, url):
    with pytest.raises(SystemExit) as exc:
        cli._cmd_share(args(relay_url=url))
    assert exc.value.code == 2
    mint.assert_not_called()
    shown = capsys.readouterr().out
    assert "--relay-url" in shown and "synthetic-password" not in shown


@pytest.mark.parametrize("url", ["http://192.168.1.20:8080", "https://relay.test",
                               "https://relay.test:443", "http://[2001:db8::5]:8787"])
def test_plain_nonloopback_origins_are_accepted(url):
    assert public_relay_url(url) == url


@pytest.mark.parametrize("relay", [LOCAL, "http://[::1]:8787", "http://localhost:8787"])
def test_local_default_prints_concrete_tunnel_fix(mint, monkeypatch, capsys, relay):
    monkeypatch.setattr(cli, "RELAY_URL", relay)
    cli._cmd_share(args())
    shown = capsys.readouterr().out
    assert f"cloudflared tunnel --url {shlex.quote(relay)}" in shown
    assert "quorus share applications --relay-url https://<the-tunnel-host>" in shown
    assert "quorus relay --host 0.0.0.0" not in shown


def test_public_default_keeps_existing_output_without_loopback_false_positive(mint,
                                                                             monkeypatch, capsys):
    monkeypatch.setattr(cli, "RELAY_URL", "https://localhost.example.com")
    cli._cmd_share(args())
    shown = capsys.readouterr().out
    assert "cloudflared" not in shown
    assert "--relay https://localhost.example.com" in shown


def test_clipboard_uses_public_address(mint, monkeypatch):
    copied = []
    monkeypatch.setattr(cli, "_copy_to_clipboard", lambda text: copied.append(text) or True)
    a = args(relay_url=PUBLIC)
    a.copy = True
    cli._cmd_share(a)
    assert copied == [f"quorus join {CODE} --relay {PUBLIC} --name "]


def test_share_parser_exposes_relay_url(monkeypatch):
    captured = []
    monkeypatch.setattr(cli, "_cmd_share", lambda a: captured.append(a))
    monkeypatch.setattr("sys.argv", ["quorus", "share", "applications", "--relay-url", PUBLIC])
    cli.main()
    assert captured[0].relay_url == PUBLIC


def test_one_trailing_slash_is_accepted(mint, capsys):
    cli._cmd_share(args(relay_url=PUBLIC + "/"))
    shown = capsys.readouterr().out
    assert f"--relay {PUBLIC}" in shown and f"{PUBLIC}//" not in shown
    assert public_relay_url(PUBLIC + "/") == PUBLIC
