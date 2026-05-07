"""Regression: wake-smoke posts the current route shapes (codex audit)."""
from __future__ import annotations

from typing import Any

import pytest

from quorus.cli import _init_run_wake_smoke
from quorus.routes.models import CreateRoomRequest, JoinLeaveRequest, RoomMessageRequest


class _Resp:
    def __init__(self, s: int, b: Any): self.status_code, self._b = s, b
    def json(self) -> Any: return self._b


def _capture(monkeypatch: pytest.MonkeyPatch, agent: str) -> tuple[list, list]:
    """Patch httpx.{post,get} to record calls and return canned responses."""
    posts, gets = [], []

    def _post(url: str, **kw: Any) -> _Resp:
        posts.append({"url": url, **kw})
        body = {"token": "jwt"} if url.endswith("/v1/auth/token") else {"id": "r1"}
        return _Resp(200 if url.endswith(("/v1/auth/token", "/join")) else 201, body)

    def _get(url: str, **kw: Any) -> _Resp:
        gets.append({"url": url, **kw})
        return _Resp(200, [{"from": agent, "from_name": agent, "content": "pong"}])

    monkeypatch.setattr("quorus.cli.httpx.post", _post)
    monkeypatch.setattr("quorus.cli.httpx.get", _get)
    return posts, gets


def test_init_wake_smoke_uses_current_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    base, agent = "arav", "arav-claude"
    posts, gets = _capture(monkeypatch, agent)
    ok, _, detail = _init_run_wake_smoke(
        relay_url="https://relay.test", human_api_key="k_h",
        agent_api_keys={agent: "k_a"}, base_name=base, ui=None, timeout_s=2.0,
    )
    assert ok and f"reply from @{agent}" in detail

    by = {p["url"].rsplit("?", 1)[0]: p for p in posts}
    create = by["https://relay.test/rooms"]["json"]
    CreateRoomRequest.model_validate(create)
    assert create["created_by"] == base

    join = by["https://relay.test/rooms/r1/join"]
    assert not join.get("params"), "join uses JSON body"
    JoinLeaveRequest.model_validate(join["json"])
    assert join["json"]["participant"] == agent

    send = by["https://relay.test/rooms/r1/messages"]["json"]
    RoomMessageRequest.model_validate(send)
    assert send["from_name"] == base and "text" not in send
    assert send["content"].startswith(f"@{agent}")

    hist = [g["url"].rsplit("?", 1)[0] for g in gets]
    assert "https://relay.test/rooms/r1/history" in hist
    assert "https://relay.test/rooms/r1/messages" not in hist


def test_init_wake_smoke_split_identity_uses_sender_for_jwt_bound_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When parent CLI name has a platform suffix (`arav-codex`), the human
    profile is registered under the stripped name (`arav`). `created_by` and
    `from_name` MUST equal the human (JWT sub), while the `@`-target stays
    bound to the parent name. Without this split, the relay 403s on every
    wake-smoke run for users who init under a suffixed identity."""
    parent, human, agent = "arav-codex", "arav", "arav-codex-claude"
    posts, _ = _capture(monkeypatch, agent)
    ok, _, _ = _init_run_wake_smoke(
        relay_url="https://relay.test", human_api_key="k_h",
        agent_api_keys={agent: "k_a"}, base_name=parent,
        sender_name=human, ui=None, timeout_s=2.0,
    )
    assert ok

    by = {p["url"].rsplit("?", 1)[0]: p for p in posts}
    create = by["https://relay.test/rooms"]["json"]
    CreateRoomRequest.model_validate(create)
    assert create["created_by"] == human, "created_by must be JWT sub (human), not parent"

    send = by["https://relay.test/rooms/r1/messages"]["json"]
    RoomMessageRequest.model_validate(send)
    assert send["from_name"] == human, "from_name must be JWT sub (human), not parent"
    assert send["content"].startswith(f"@{agent}"), "@-target stays bound to parent"

    join = by["https://relay.test/rooms/r1/join"]["json"]
    JoinLeaveRequest.model_validate(join)
    assert join["participant"] == agent
