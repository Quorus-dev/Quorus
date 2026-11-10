"""The terminal app's share card: it leads with the invite link when the relay
serves one, and says so when the relay can't be reached from anywhere else."""
from __future__ import annotations

import io

import httpx
import pytest
from quorus_tui import chat as _chat
from quorus_tui import hub
from rich.console import Console

CLOUD = "https://cloud.example.test"
LOCAL = "http://127.0.0.1:8080"
CODE = "ABCD-EFGH"


@pytest.fixture
def console():
    return Console(file=io.StringIO(), width=100, force_terminal=False, no_color=True,
                   legacy_windows=False)


def _relay(monkeypatch, body: dict | None, status: int = 200):
    def post(url, **kw):
        if body is None:
            raise httpx.ConnectError("refused")
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(hub.httpx, "post", post)
    monkeypatch.setattr(hub, "_operator_credential", lambda s: s)


def _show(monkeypatch, console, relay_url, invite, key="x"):
    copied: list[str] = []
    monkeypatch.setattr(_chat, "copy_to_clipboard", lambda t: copied.append(t) or True)
    monkeypatch.setattr("quorus_tui.slash._default_one_key", lambda: key)
    status = hub._share_card(console, "launch", invite, relay_url)
    return console.file.getvalue(), status, copied


def test_mint_keeps_the_link_a_relay_returns(monkeypatch):
    _relay(monkeypatch, {"code": CODE, "page_url": f"{CLOUD}/j/{CODE}"})
    assert hub._mint_invite(CLOUD, "k", "launch") == {
        "code": CODE, "page_url": f"{CLOUD}/j/{CODE}"}
    assert hub._mint_join_token(CLOUD, "k", "launch") == CODE


def test_an_older_relay_gives_a_code_and_no_link(monkeypatch):
    _relay(monkeypatch, {"code": CODE})
    assert hub._mint_invite(CLOUD, "k", "launch") == {"code": CODE, "page_url": ""}


@pytest.mark.parametrize("body,status", [(None, 200), ({"detail": "no"}, 500), ({}, 200)])
def test_a_failed_mint_is_empty_and_never_a_made_up_invite(monkeypatch, body, status):
    _relay(monkeypatch, body, status)
    assert hub._mint_invite(CLOUD, "k", "launch") == {}
    assert hub._mint_join_token(CLOUD, "k", "launch") == ""


def test_the_card_leads_with_the_link_and_c_copies_it(monkeypatch, console):
    link = f"{CLOUD}/j/{CODE}"
    out, status, copied = _show(monkeypatch, console, CLOUD,
                                {"code": CODE, "page_url": link}, key="c")
    assert out.index(link) < out.index(f"Code:   {CODE}")
    assert "press c to copy the link" in out
    assert copied == [link] and "invite link" in status
    assert "quorus cloud" not in out
    # Every row of the card is the same width: the link didn't push the border out.
    rows = [line for line in out.splitlines() if line.strip().startswith(("│", "┌", "└"))]
    assert len({len(row.rstrip()) for row in rows}) == 1


def test_without_a_link_the_card_is_the_code_as_before(monkeypatch, console):
    out, status, copied = _show(monkeypatch, console, CLOUD,
                                {"code": CODE, "page_url": ""}, key="c")
    assert "Link:" not in out and f"quorus join {CODE}" in out
    assert copied == [CODE] and "link" not in status


@pytest.mark.parametrize("relay", [LOCAL, "http://localhost:8080", "http://[::1]:8080"])
def test_a_laptop_relay_points_to_quorus_cloud_and_hands_out_no_address(
        monkeypatch, console, relay):
    """Its link and its curl one-liner would both open nothing on another computer."""
    out, _, copied = _show(monkeypatch, console, relay,
                           {"code": CODE, "page_url": f"{relay}/j/{CODE}"}, key="c")
    assert "people elsewhere can't reach it" in out and "quorus cloud" in out
    assert "Link:" not in out and "curl" not in out and "/j/" not in out
    assert copied == [CODE]


def test_the_join_line_names_a_relay_a_bare_code_wouldnt_find(monkeypatch, console):
    monkeypatch.setenv("QUORUS_PUBLIC_RELAY", CLOUD)
    hosted, _, _ = _show(monkeypatch, console, CLOUD + "/", {"code": CODE, "page_url": ""})
    assert f"quorus join {CODE} " in hosted and "--relay" not in hosted
    console.file.truncate(0)
    lan, _, _ = _show(monkeypatch, console, "http://10.0.0.5:8080",
                      {"code": CODE, "page_url": ""})
    assert f"quorus join {CODE} --relay http://10.0.0.5:8080" in lan


def test_a_forged_link_from_a_relay_is_not_shown(monkeypatch, console):
    out, _, copied = _show(
        monkeypatch, console, CLOUD,
        {"code": CODE, "page_url": f"https://evil.test/j/{CODE}?x=$(id)"}, key="c")
    assert "evil.test" not in out and copied == [CODE]
