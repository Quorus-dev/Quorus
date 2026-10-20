"""0.6.3 audit (multi-person lane): an observer's CLI offered things they can't do."""

import httpx
import pytest
from quorus_cli import agent_cmd, cli


def test_an_observers_next_steps_offer_only_what_they_can_do() -> None:
    steps = " ".join(cli._join_next_steps("design", observer=True))
    assert "quorus say" not in steps and "agent add" not in steps
    assert "quorus history design" in steps and "member invite" in steps
    member = " ".join(cli._join_next_steps("design", observer=False))
    assert "quorus say design" in member and "agent add claude --room design" in member


def test_an_observers_agent_add_says_observer_not_bad_credentials(monkeypatch) -> None:
    cfg = {"relay_url": "http://relay", "instance_name": "watcher"}

    def post(url, **kw):
        return httpx.Response(403, json={"detail": "Only room creator or admin can add members."},
                              request=httpx.Request("POST", url))

    def get(url, **kw):
        return httpx.Response(200, json={"member_roles": {"alice": "admin", "watcher": "observer"}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(agent_cmd.httpx, "post", post)
    monkeypatch.setattr(agent_cmd.httpx, "get", get)
    with pytest.raises(SystemExit) as exc:
        agent_cmd._join(cfg, "obsroom", "watcher-opencode", "k")
    assert "as an observer" in str(exc.value) and "member invite" in str(exc.value)


def test_a_members_403_still_raises_for_the_usual_handler(monkeypatch) -> None:
    cfg = {"relay_url": "http://relay", "instance_name": "bob"}
    monkeypatch.setattr(agent_cmd.httpx, "post", lambda url, **kw: httpx.Response(
        403, json={"detail": "nope"}, request=httpx.Request("POST", url)))
    monkeypatch.setattr(agent_cmd.httpx, "get", lambda url, **kw: httpx.Response(
        200, json={"member_roles": {"bob": "member"}}, request=httpx.Request("GET", url)))
    with pytest.raises(httpx.HTTPStatusError):
        agent_cmd._join(cfg, "r", "bob-codex", "k")
