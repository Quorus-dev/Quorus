"""Gate by default, slice A: an agent's own key and pin before its daemon starts
(docs/plans/gate-by-default.md, Codex plan GO eb25acef)."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from quorus_cli import agent_identity as ai

from quorus import bundle_client

URL = "http://relay.test"


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class _Http:
    def __init__(self, *answers) -> None:
        self.answers, self.calls = list(answers), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        status, body = self.answers.pop(0)
        return SimpleNamespace(status_code=status, json=lambda: body)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path


def test_key_dir_is_where_the_daemon_looks(home):
    assert ai.key_dir("a-codex") == home / "cfg" / "runtime" / "agents" / "a-codex"
    state = home / "state"
    assert ai.key_dir("a-codex", state) == state / "runtime" / "agents" / "a-codex"


def test_ensure_key_claims_once_and_saves_where_the_daemon_loads_it(home, monkeypatch):
    http = _Http((200, {"participant_key": "quorus_pk_x"}))
    monkeypatch.setattr(ai, "_relay_http", http)
    assert ai.ensure_key(URL, "sec", "a-codex") == "quorus_pk_x"
    url, kw = http.calls[0]
    assert url == f"{URL}/v1/auth/claim-name" and kw["json"] == {"name": "a-codex"}
    assert kw["headers"] == {"Authorization": "Bearer sec"} and kw["follow_redirects"] is False
    saved = bundle_client.load_participant_key(URL, "a-codex", ai.key_dir("a-codex"))
    assert saved == "quorus_pk_x"
    assert ai.ensure_key(URL, "sec", "a-codex") == "quorus_pk_x"  # reused, no second claim
    assert len(http.calls) == 1


@pytest.mark.parametrize("status, why", [(409, "already claimed"), (403, "HTTP 403"),
                                         (307, "HTTP 307")])
def test_ensure_key_refuses_honestly(home, monkeypatch, status, why):
    monkeypatch.setattr(ai, "_relay_http", _Http((status, {})))
    with pytest.raises(ai.IdentityError, match=why):
        ai.ensure_key(URL, "sec", "a-codex")
    assert bundle_client.load_participant_key(URL, "a-codex", ai.key_dir("a-codex")) == ""


def test_identity_reads_the_claim_from_the_agents_own_token(monkeypatch):
    token = _jwt({"sub": "a-codex", "tenant_id": "_legacy", "pk_claim": "7"})
    http = _Http((200, {"token": token}))
    monkeypatch.setattr(ai, "_relay_http", http)
    assert ai.identity(URL, "quorus_pk_x") == {"tenant": "_legacy", "claim_id": "7"}
    assert http.calls[0][1]["follow_redirects"] is False


@pytest.mark.parametrize("answer", [(401, {}), (200, {"token": "nodots"}),
                                    (200, {"token": _jwt({"tenant_id": "_legacy"})})])
def test_identity_without_a_claim_is_an_error(monkeypatch, answer):
    monkeypatch.setattr(ai, "_relay_http", _Http(answer))
    with pytest.raises(ai.IdentityError):
        ai.identity(URL, "quorus_pk_x")
