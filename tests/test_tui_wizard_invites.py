"""The first-run wizard joins with a secret-free invite (codex, first-run audit).

A v2 invite carries no secret, only a code that redeems into the joiner's own
participant key. The wizard used to save the empty secret and drop the code, so
the invited teammate landed unauthenticated, and the hub only exchanged mct_
keys, so a quorus_pk_ key was never turned into a JWT.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from quorus_tui import hub
from rich.console import Console


def _resp(status: int, body: dict) -> MagicMock:
    r = MagicMock(status_code=status, content=b"x")
    r.json.return_value = body
    return r


def _run_wizard(monkeypatch, *, redeem_status=200):
    answers = iter(["ABCD-EFGH", "http://relay.test:8080"])
    monkeypatch.setattr(hub, "_prompt_name", lambda console: "bob")
    monkeypatch.setattr(hub.Prompt, "ask", lambda *a, **kw: next(answers))
    payload = {"r": "http://relay.test:8080", "n": "build", "c": "qi_" + "x" * 30, "v": 2}
    monkeypatch.setattr(hub.httpx, "get", lambda url, **kw: _resp(200, {"payload": payload}))
    redeemed = []

    def fake_post(url, json=None, **kw):
        redeemed.append((url, json))
        return _resp(redeem_status, {"participant_key": "quorus_pk_bobkey", "room": "build"}
                     if redeem_status == 200 else {"detail": "no"})

    import quorus_cli.cli as cli

    monkeypatch.setattr(cli.httpx, "post", fake_post)
    saved = []
    monkeypatch.setattr(hub, "_save_instance_config",
                        lambda *a, **kw: saved.append((a, kw)))
    out = hub._first_launch_setup(Console(file=open("/dev/null", "w")))
    return out, redeemed, saved


def test_the_wizard_redeems_an_invite_code_into_its_own_key(monkeypatch) -> None:
    profile, redeemed, saved = _run_wizard(monkeypatch)
    assert redeemed == [("http://relay.test:8080/v1/join/redeem",
                         {"code": "qi_" + "x" * 30, "name": "bob"})]
    assert profile["api_key"] == "quorus_pk_bobkey" and profile["relay_secret"] == ""
    assert profile["room"] == "build"
    assert saved and saved[0][1]["api_key"] == "quorus_pk_bobkey"


def test_a_refused_invite_saves_nothing(monkeypatch) -> None:
    profile, _, saved = _run_wizard(monkeypatch, redeem_status=403)
    assert profile == {} and saved == []


def test_a_participant_key_is_exchanged_for_a_jwt(monkeypatch) -> None:
    hub._jwt_cache.clear()
    monkeypatch.setattr(hub.httpx, "post",
                        lambda url, **kw: _resp(200, {"token": "jwt-for-bob"}))
    assert hub._get_auth_token("http://relay.test:8080", "quorus_pk_bobkey") == "jwt-for-bob"
    hub._jwt_cache.clear()
