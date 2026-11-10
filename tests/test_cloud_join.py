"""Joining from anywhere: a bare invite code finds Quorus Cloud, and the first-run
wizard can make an account there (quorus/cloud.py)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
import pytest
from quorus_cli import cli

CLOUD = "https://cloud.example.test"
LOCAL = "http://127.0.0.1:8787"
PAYLOAD = {"r": CLOUD, "n": "launch", "t": "tenant-1", "i": "invite-jwt"}


def _get(answers: dict):
    """A fake httpx.get: each relay origin answers with a status, or raises."""
    asked: list[str] = []

    def get(url, **kw):
        origin = url.split("/v1/join/resolve/")[0]
        asked.append(origin)
        answer = answers[origin]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    return get, asked


def _join(monkeypatch, answers: dict, *, relay=None, configured=LOCAL):
    monkeypatch.setenv("QUORUS_PUBLIC_RELAY", CLOUD)
    monkeypatch.setattr(cli, "load_config", lambda: {"relay_url": configured})
    get, asked = _get(answers)
    monkeypatch.setattr(cli.httpx, "get", get)
    applied: list[tuple] = []
    monkeypatch.setattr(cli, "_apply_join_payload", lambda p, n: applied.append((p, n)))
    cli._cmd_join(argparse.Namespace(token="XNH8-5D8Y", relay_url=relay, name="bob",
                                     room=None, secret=None, api_key=None))
    return asked, applied


def test_a_code_from_quorus_cloud_needs_no_relay_flag(monkeypatch):
    asked, [(payload, name)] = _join(monkeypatch, {
        LOCAL: (404, {"detail": "code not found or expired"}),
        CLOUD: (200, {"payload": PAYLOAD, "code": "XNH85D8Y"})})
    assert asked == [LOCAL, CLOUD]
    assert payload["r"] == CLOUD and payload["i"] == "invite-jwt" and name == "bob"


def test_a_code_on_this_machines_relay_never_goes_to_the_cloud(monkeypatch):
    local = {"r": LOCAL, "n": "launch", "c": "qi_synthetic", "v": 2}
    asked, [(payload, _)] = _join(monkeypatch, {LOCAL: (200, {"payload": local})})
    assert asked == [LOCAL] and payload["r"] == LOCAL


def test_a_dead_local_relay_doesnt_stop_a_cloud_code(monkeypatch):
    asked, [(payload, _)] = _join(monkeypatch, {
        LOCAL: httpx.ConnectError("refused"),
        CLOUD: (200, {"payload": PAYLOAD})})
    assert asked == [LOCAL, CLOUD] and payload["r"] == CLOUD


def test_an_explicit_relay_is_the_only_place_looked(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _join(monkeypatch, {"https://their.relay": (404, {})}, relay="https://their.relay")
    assert exc.value.code == 4


def test_a_code_nowhere_is_not_found(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _join(monkeypatch, {LOCAL: (404, {}), CLOUD: (404, {})})
    assert exc.value.code == 4
    assert "not found" in capsys.readouterr().out.lower()


def test_nothing_reachable_says_so(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _join(monkeypatch, {LOCAL: httpx.ConnectError("x"), CLOUD: httpx.ConnectError("y")})
    assert exc.value.code == 2


# ── first run: `cloud` at the wizard's one prompt ───────────────────────────────


def _wizard(monkeypatch, tmp_path: Path, typed: str, *, get=None, post=None):
    from quorus_tui import hub
    from rich.console import Console

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / ".quorus"))
    monkeypatch.setenv("RELAY_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("QUORUS_PUBLIC_RELAY", CLOUD)
    monkeypatch.setattr(hub, "_prompt_name", lambda console: "alice")
    monkeypatch.setattr(hub.Prompt, "ask", staticmethod(lambda *a, **k: typed))
    asked_address: list[bool] = []
    monkeypatch.setattr(hub, "_ask_relay_address",
                        lambda console, tries=3: asked_address.append(True) or "")
    monkeypatch.setattr(hub, "_try_connect", lambda url, timeout=10: True)
    from quorus.runtime import local_relay

    monkeypatch.setattr(local_relay, "ensure_local_relay",
                        lambda url, secret: pytest.fail("cloud must not start a local relay"))
    from quorus import cloud

    if get:
        monkeypatch.setattr(hub.httpx, "get", get)
    if post:
        monkeypatch.setattr(cloud.httpx, "post", post)
    with open(tmp_path / "out.txt", "w") as fh:
        profile = hub._first_launch_setup(Console(file=fh, width=400))
    saved = [json.loads(f.read_text()) for f in (tmp_path / ".quorus").rglob("*.json")]
    return profile, (tmp_path / "out.txt").read_text(), asked_address, saved


def _signed_up(sent: list[dict], key: str):
    def post(url, **kw):
        sent.append({"url": url, **kw})
        return httpx.Response(200, json={"api_key": key},
                              request=httpx.Request("POST", url))
    return post


def test_typing_cloud_makes_an_account_there(monkeypatch, tmp_path):
    sent: list[dict] = []
    profile, out, _, saved = _wizard(monkeypatch, tmp_path, "cloud",
                                     post=_signed_up(sent, "mct_alice"))
    assert [s["url"] for s in sent] == [f"{CLOUD}/v1/auth/signup"]
    assert profile["relay_url"] == CLOUD and profile["api_key"] == "mct_alice"
    assert not profile["relay_secret"]
    assert any(d.get("api_key") == "mct_alice" and d.get("relay_url") == CLOUD
               for d in saved if isinstance(d, dict))
    assert "Quorus Cloud" in out


def test_a_bare_code_from_the_cloud_joins_without_asking_for_an_address(monkeypatch,
                                                                         tmp_path):
    get, asked = _get({CLOUD: (200, {"payload": PAYLOAD})})
    sent: list[dict] = []
    profile, _, asked_address, _ = _wizard(monkeypatch, tmp_path, "XNH8-5D8Y", get=get,
                                           post=_signed_up(sent, "mct_bob"))
    assert asked == [CLOUD] and asked_address == []
    [join] = sent
    assert join["json"]["join_tenant_id"] == "tenant-1"
    assert join["json"]["invite_token"] == "invite-jwt"
    assert profile["relay_url"] == CLOUD and profile["api_key"] == "mct_bob"
    assert profile["room"] == "launch"


def test_a_code_not_on_the_cloud_still_asks_for_the_relay(monkeypatch, tmp_path):
    get, _ = _get({CLOUD: (404, {})})
    profile, _, asked_address, _ = _wizard(monkeypatch, tmp_path, "XNH8-5D8Y", get=get)
    assert asked_address == [True] and profile == {}
