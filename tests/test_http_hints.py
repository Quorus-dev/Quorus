"""U1 (0.5.1, 0.5.2): the CLI and the MCP tools say why the relay refused.

0.5.0 printed "Could not create room · server returned HTTP 422", "hint: HTTP
413" and "hint: HTTP 429" (overseer's agent sweep).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from quorus_cli.http_hints import hint

from quorus.relay import _reset_state, app

ADMIN = {"Authorization": "Bearer test-secret"}


@pytest.fixture
def client():
    _reset_state()
    yield TestClient(app)
    _reset_state()


def test_a_bad_room_name_names_the_rule(client) -> None:
    r = client.post("/rooms", json={"name": "my room!", "created_by": "arav"}, headers=ADMIN)
    assert r.status_code == 422
    assert "alphanumeric characters, hyphens, and underscores" in hint(r)
    assert "Value error" not in hint(r)


def test_a_too_big_message_names_the_limit(client) -> None:
    rid = client.post("/rooms", json={"name": "big", "created_by": "arav"},
                      headers=ADMIN).json()["id"]
    r = client.post(f"/rooms/{rid}/messages", headers=ADMIN,
                    json={"from_name": "arav", "content": "x" * 60_000})
    assert r.status_code == 413
    assert "up to 50 KB" in hint(r)


def test_a_rate_limit_says_when_to_retry(client) -> None:
    for i in range(10):
        client.post("/rooms", json={"name": f"r{i}", "created_by": "arav"}, headers=ADMIN)
    r = client.post("/rooms", json={"name": "r10", "created_by": "arav"}, headers=ADMIN)
    assert r.status_code == 429
    assert r.headers["retry-after"].isdigit()
    assert hint(r).startswith("the relay is rate-limiting you: try again in ")


def test_no_body_still_gives_the_code() -> None:
    class R:
        status_code = 502
        headers: dict = {}

        def json(self):
            raise ValueError

    assert hint(R()) == "HTTP 502"


def _mcp_error(resp) -> str:
    import httpx
    from quorus_mcp import server

    err = httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://t"),
                                response=httpx.Response(resp.status_code,
                                                        headers=dict(resp.headers),
                                                        content=resp.content))
    return server._relay_error_message(err)


def test_mcp_tools_say_why_too(client) -> None:
    """0.5.1 live check: agents got 'Relay returned 429: {...}' with no retry time."""
    for i in range(10):
        client.post("/rooms", json={"name": f"m{i}", "created_by": "arav"}, headers=ADMIN)
    limited = client.post("/rooms", json={"name": "m10", "created_by": "arav"}, headers=ADMIN)
    assert _mcp_error(limited).startswith("Error: the relay is rate-limiting you: try again in ")
    bad = client.post("/rooms", json={"name": "a b", "created_by": "arav"}, headers=ADMIN)
    assert "alphanumeric" in _mcp_error(bad) and "{" not in _mcp_error(bad)


def test_the_relay_itself_says_when_to_retry(client) -> None:
    """Any client (curl, an SDK, another harness) sees the wait, not a bare refusal."""
    for i in range(11):
        r = client.post("/rooms", json={"name": f"n{i}", "created_by": "arav"}, headers=ADMIN)
    assert r.status_code == 429
    assert r.json()["detail"] == f"Rate limit exceeded: try again in {r.headers['retry-after']}s"
