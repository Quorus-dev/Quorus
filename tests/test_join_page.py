"""The invite page (GET /j/{code}): a link an inviter sends, and what
`quorus share` and `quorus join` say around it."""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from quorus_cli import cli, ui

from quorus.cloud import SignupError, signup, signup_limit_message
from quorus.relay import _reset_state, app
from quorus.services.join_code_svc import invite_page_url

ADMIN = {"Authorization": "Bearer test-secret"}
CLOUD = "https://cloud.example.test"
CODE = "ABCD-EFGH"


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _mint(client: AsyncClient, room: str = "launch") -> dict:
    made = await client.post("/rooms", headers=ADMIN, json={"name": room, "created_by": "admin"})
    assert made.status_code in (200, 201), made.text
    resp = await client.post("/v1/join/mint", headers=ADMIN, json={"room": room})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_the_page_says_who_invited_you_where_and_how_to_join(client):
    minted = await _mint(client)
    assert minted["page_url"] == f"http://test/j/{minted['code']}"
    resp = await client.get(f"/j/{minted['code']}")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")
    page = resp.text
    assert "You&#x27;re invited to <b>#launch</b>" in page
    assert "<title>Join #launch on Quorus</title>" in page
    assert "pipx install quorus-ai" in page
    assert f"quorus join {minted['code']} --name" in page and "--relay http://test" in page
    assert f"http://test/v1/join/install/{minted['code']}.sh | bash" in page
    assert "noindex" in page


async def test_the_page_never_carries_the_invite_itself(client):
    """The payload holds what redeems the invite; the page shows the short code only."""
    minted = await _mint(client)
    payload = (await client.get(f"/v1/join/resolve/{minted['code']}")).json()["payload"]
    secret_part = payload.get("c") or payload.get("i")
    assert secret_part
    page = (await client.get(f"/j/{minted['code']}")).text
    assert secret_part not in page and "test-secret" not in page


async def test_opening_the_page_spends_nothing(client):
    """Chat apps fetch a link to preview it, often several times."""
    minted = await _mint(client)
    for _ in range(5):
        assert (await client.get(f"/j/{minted['code']}")).status_code == 200
    payload = (await client.get(f"/v1/join/resolve/{minted['code']}")).json()["payload"]
    redeemed = await client.post("/v1/join/redeem",
                                 json={"code": payload["c"], "name": "bob"})
    assert redeemed.status_code == 200, redeemed.text


@pytest.mark.parametrize("code", ["ZZZZ-ZZZZ", "nope", "HX4K-M0ZP", "%3Cscript%3E"])
async def test_an_unknown_or_malformed_code_gets_a_plain_404_page(client, code):
    resp = await client.get(f"/j/{code}")
    assert resp.status_code == 404
    assert "isn't valid any more" in resp.text and "<script>alert" not in resp.text
    assert "quorus join" not in resp.text


async def test_an_expired_code_is_gone(client):
    svc = app.state.join_code_service
    display, _ = await svc.mint(tenant_id="_legacy", room_id="r1", room_name="launch",
                                payload={"r": "http://test", "n": "launch"}, ttl_seconds=60,
                                created_by="arav")
    live = await client.get(f"/j/{display}")
    assert live.status_code == 200 and "@arav invited you to <b>#launch</b>" in live.text
    entry = svc._mem[display.replace("-", "")]
    entry["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert (await client.get(f"/j/{display}")).status_code == 404
    assert await svc.describe(display) is None


async def test_nothing_from_the_invite_is_trusted_as_html(client):
    svc = app.state.join_code_service
    display, _ = await svc.mint(
        tenant_id="_legacy", room_id="r1", room_name='<img src=x onerror="alert(1)">',
        payload={"r": "http://test", "n": "x"}, ttl_seconds=60,
        created_by='"><script>alert(1)</script>')
    resp = await client.get(f"/j/{display}", headers={
        "x-forwarded-host": 'evil.test"><script>alert(2)</script>'})
    page = resp.text
    assert resp.status_code == 200
    assert "<img" not in page and "alert(1)" not in page.replace("&quot;alert(1)&quot;", "")
    assert "alert(2)" not in page and "evil.test" not in page
    assert "You&#x27;re invited to" in page


async def test_the_page_runs_only_its_own_script(client):
    minted = await _mint(client)
    resp = await client.get(f"/j/{minted['code']}")
    csp = resp.headers["content-security-policy"]
    nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", csp).group(1)
    assert "default-src 'none'" in csp and "unsafe-inline" not in csp
    assert f'<script nonce="{nonce}">' in resp.text and f'<style nonce="{nonce}">' in resp.text
    assert resp.text.count("<script") == 1
    assert resp.headers["cache-control"] == "no-store"
    again = await client.get(f"/j/{minted['code']}")
    assert nonce not in again.headers["content-security-policy"]


async def test_describe_names_the_room_and_inviter_but_not_the_payload(client):
    svc = app.state.join_code_service
    display, expires = await svc.mint(
        tenant_id="t1", room_id="r1", room_name="launch",
        payload={"r": "http://test", "n": "launch", "i": "invite-jwt"},
        ttl_seconds=3600, created_by="arav")
    assert await svc.describe(display.lower()) == {
        "room_name": "launch", "created_by": "arav", "expires_at": expires}
    assert await svc.describe("not a code") is None


# ---- the link a relay hands back --------------------------------------------


@pytest.mark.parametrize("url", [f"{CLOUD}/j/{CODE}", f"http://10.0.0.5:8080/j/{CODE}",
                                 f"{CLOUD}/j/abcdefgh"])
def test_a_plain_invite_link_is_kept(url):
    assert invite_page_url(url) == url


@pytest.mark.parametrize("url", [
    "", f"ftp://x.test/j/{CODE}", f"{CLOUD}/j/nope", f"{CLOUD}/x/j/{CODE}",
    f"{CLOUD}/j/{CODE}?next=//evil.test", f"{CLOUD}/j/{CODE}#x", f"{CLOUD}/v1/join/{CODE}",
    f"https://$(id).test/j/{CODE}", f"https://x.test/j/{CODE}\n", f"javascript:alert(1)//j/{CODE}",
    f"https:///j/{CODE}",
])
def test_anything_else_from_a_relay_is_dropped(url):
    assert invite_page_url(url) is None


# ---- quorus share -------------------------------------------------------------


def _share(monkeypatch, capsys, *, relay, body, **kwargs) -> str:
    monkeypatch.setattr(cli, "RELAY_URL", relay)
    monkeypatch.setattr(cli, "RELAY_SECRET", "synthetic-secret")
    monkeypatch.setattr(cli, "API_KEY", "")
    monkeypatch.setattr(cli, "_admin_headers", lambda: {"Authorization": "Bearer x"})
    response = httpx.Response(200, request=httpx.Request("POST", relay + "/v1/join/mint"),
                             json={"code": CODE, **body})
    monkeypatch.setattr(cli.httpx, "post", Mock(return_value=response))
    monkeypatch.setattr(ui.console, "width", 400)
    cli._cmd_share(argparse.Namespace(room="launch", ttl=1, uses=1, copy=False,
                                      observer=False, relay_url=kwargs.get("relay_url")))
    return capsys.readouterr().out


def test_share_leads_with_the_link_when_the_relay_serves_the_page(monkeypatch, capsys):
    shown = _share(monkeypatch, capsys, relay=CLOUD, body={"page_url": f"{CLOUD}/j/{CODE}"})
    lines = [line.strip() for line in shown.splitlines()]
    assert lines.index(f"{CLOUD}/j/{CODE}") < next(
        i for i, line in enumerate(lines) if line.startswith("quorus join "))


def test_share_follows_the_advertised_address(monkeypatch, capsys):
    shown = _share(monkeypatch, capsys, relay="http://127.0.0.1:8787",
                   body={"page_url": f"http://127.0.0.1:8787/j/{CODE}"},
                   relay_url="https://relay.example.test")
    assert f"https://relay.example.test/j/{CODE}" in shown and "127.0.0.1" not in shown


def test_share_shows_no_link_for_an_older_relay_or_a_laptop_one(monkeypatch, capsys):
    older = _share(monkeypatch, capsys, relay=CLOUD, body={})
    assert "/j/" not in older and "quorus join " in older
    laptop = _share(monkeypatch, capsys, relay="http://127.0.0.1:8787",
                    body={"page_url": f"http://127.0.0.1:8787/j/{CODE}"})
    assert "/j/" not in laptop
    forged = _share(monkeypatch, capsys, relay=CLOUD,
                    body={"page_url": f"https://evil.test/j/{CODE}?x=$(id)"})
    assert "evil.test" not in forged


# ---- a 429 at signup ----------------------------------------------------------


def _limited(retry_after: str | None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return httpx.Response(429, json={"detail": "Too many signup attempts"}, headers=headers,
                          request=httpx.Request("POST", CLOUD + "/v1/auth/signup"))


def test_a_minute_limit_says_wait_and_a_daily_cap_says_tomorrow():
    assert "wait a minute" in signup_limit_message(_limited("60"))
    assert "wait a minute" in signup_limit_message(_limited(None))
    assert "tomorrow" in signup_limit_message(_limited("86400"))
    assert "wait a minute" in signup_limit_message(_limited("soon"))


def test_quorus_cloud_reports_the_limit_in_words(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _limited("60"))
    with pytest.raises(SignupError) as exc:
        signup(CLOUD, "bob")
    assert "wait a minute" in str(exc.value) and "HTTP 429" not in str(exc.value)


def test_a_rate_limited_join_says_the_invite_is_still_good(monkeypatch, capsys, tmp_path):
    """It used to say "ask your teammate for a fresh code", which wouldn't have helped:
    the limit is hit before the invite is looked at."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / ".quorus"))
    monkeypatch.setattr(cli.httpx, "post", lambda *a, **kw: _limited("60"))
    monkeypatch.setattr(ui.console, "width", 400)
    with pytest.raises(SystemExit) as exc:
        cli._apply_join_payload(
            {"r": CLOUD, "n": "launch", "t": "tenant-1", "i": "invite-jwt"}, "bob")
    assert exc.value.code == 4
    shown = " ".join(capsys.readouterr().out.split())
    assert "wait a minute" in shown and "your invite is still good" in shown
    assert "fresh code" not in shown
    assert not (tmp_path / ".quorus").exists()
