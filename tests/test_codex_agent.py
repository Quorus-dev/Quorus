from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from quorus_cli.codex_agent import (
    CodexAgentError,
    _cached_child_api_key,
    _message_id,
    _save_child_api_key,
    build_codex_command,
    build_codex_exec_command,
    build_prompt,
    parent_join_room,
    resolve_identity,
    run_codex_agent,
    save_codex_runner_defaults,
    send_heartbeat,
)


def test_resolve_identity_returns_parent_when_no_override() -> None:
    name, api_key = resolve_identity(
        relay_url="https://relay.test",
        parent_name="arav-codex",
        parent_api_key="mct_parent",
        requested_name=None,
        suffix=None,
    )

    assert name == "arav-codex"
    assert api_key == "mct_parent"


def test_resolve_identity_registers_child_from_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.json.return_value = {
        "agent_name": "arav-codex-reviewer",
        "api_key": "mct_child",
    }
    response.raise_for_status.return_value = None

    def fake_post(url: str, **kwargs):
        assert url == "https://relay.test/v1/auth/register-agent"
        assert kwargs["json"] == {"suffix": "reviewer"}
        assert kwargs["headers"]["Authorization"] == "Bearer mct_parent"
        return response

    monkeypatch.setattr("quorus_cli.codex_agent.httpx.post", fake_post)

    name, api_key = resolve_identity(
        relay_url="https://relay.test",
        parent_name="arav-codex",
        parent_api_key="mct_parent",
        requested_name=None,
        suffix="reviewer",
    )

    assert name == "arav-codex-reviewer"
    assert api_key == "mct_child"


def test_resolve_identity_uses_cached_child_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "quorus_cli.codex_agent._cached_child_api_key",
        lambda agent_name: "mct_cached" if agent_name == "arav-codex-reviewer" else "",
    )

    name, api_key = resolve_identity(
        relay_url="https://relay.test",
        parent_name="arav-codex",
        parent_api_key="mct_parent",
        requested_name=None,
        suffix="reviewer",
    )

    assert name == "arav-codex-reviewer"
    assert api_key == "mct_cached"


def test_resolve_identity_rejects_non_child_name() -> None:
    with pytest.raises(CodexAgentError):
        resolve_identity(
            relay_url="https://relay.test",
            parent_name="arav-codex",
            parent_api_key="mct_parent",
            requested_name="somebody-else",
            suffix=None,
        )


def test_build_prompt_mentions_room_and_inbox() -> None:
    prompt = build_prompt(
        "medbuddy-sprint",
        "arav-codex-reviewer",
        Path("/tmp/quorus-arav-codex-reviewer-inbox.txt"),
    )

    assert "medbuddy-sprint" in prompt
    assert "arav-codex-reviewer" in prompt
    assert "/tmp/quorus-arav-codex-reviewer-inbox.txt" in prompt


def test_build_prompt_mentions_context_snapshot_when_present() -> None:
    prompt = build_prompt(
        "medbuddy-sprint",
        "arav-codex-reviewer",
        Path("/tmp/quorus-arav-codex-reviewer-inbox.txt"),
        Path("/tmp/quorus-arav-codex-reviewer-context.md"),
    )

    assert "/tmp/quorus-arav-codex-reviewer-context.md" in prompt


def test_build_codex_command_includes_quorus_overrides() -> None:
    cmd = build_codex_command(
        room="medbuddy-sprint",
        participant="arav-codex-reviewer",
        relay_url="https://relay.test",
        api_key="mct_child",
        cwd=Path("/tmp/workspace"),
        sandbox="workspace-write",
        approval="on-request",
        inbox_path=Path("/tmp/quorus-arav-codex-reviewer-inbox.txt"),
    )

    assert cmd[:3] == [
        "codex",
        "-C",
        "/tmp/workspace",
    ]
    assert "-s" in cmd
    assert "workspace-write" in cmd
    assert "-a" in cmd
    assert "on-request" in cmd
    assert 'mcp_servers.quorus.env.QUORUS_INSTANCE_NAME="arav-codex-reviewer"' in cmd
    assert 'mcp_servers.quorus.env.QUORUS_API_KEY="mct_child"' in cmd
    assert 'mcp_servers.quorus.env.QUORUS_RELAY_URL="https://relay.test"' in cmd
    assert "medbuddy-sprint" in cmd[-1]


def test_build_codex_exec_command_uses_exec_mode() -> None:
    cmd = build_codex_exec_command(
        room="medbuddy-sprint",
        participant="arav-codex-reviewer",
        relay_url="https://relay.test",
        api_key="mct_child",
        cwd=Path("/tmp/workspace"),
        sandbox="workspace-write",
        inbox_path=Path("/tmp/quorus-arav-codex-reviewer-inbox.txt"),
        context_path=Path("/tmp/quorus-arav-codex-reviewer-context.md"),
        prompt="Handle the newest room activity.",
    )

    assert cmd[:4] == ["codex", "exec", "-C", "/tmp/workspace"]
    assert "--skip-git-repo-check" in cmd
    assert any("QUORUS_API_KEY" in part for part in cmd)
    assert "Handle the newest room activity." in cmd[-1]


def test_build_codex_command_supports_relay_secret() -> None:
    cmd = build_codex_command(
        room="dev-room",
        participant="local-codex",
        relay_url="http://localhost:8080",
        relay_secret="dev-secret",
        cwd=Path("/tmp/workspace"),
        sandbox="workspace-write",
        approval="on-request",
        inbox_path=Path("/tmp/quorus-local-codex-inbox.txt"),
    )

    assert 'mcp_servers.quorus.env.QUORUS_RELAY_SECRET="dev-secret"' in cmd
    assert all("QUORUS_API_KEY" not in part for part in cmd)


def test_message_id_prefers_message_id_then_id() -> None:
    assert _message_id({"message_id": "abc", "id": "fallback"}) == "abc"
    assert _message_id({"id": "fallback"}) == "fallback"
    assert _message_id({}) == ""


def test_save_and_load_child_key(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePm:
        def __init__(self):
            self.saved = None

        def current(self):
            return "default"

        def get(self, slug):
            assert slug == "default"
            return {"instance_name": "arav"}

        def save(self, slug, data):
            self.saved = (slug, data)

    fake_pm = FakePm()
    monkeypatch.setattr("quorus_cli.codex_agent.ProfileManager", lambda: fake_pm)

    _save_child_api_key("arav-codex-builder", "mct_child")

    assert fake_pm.saved is not None
    saved_slug, saved_data = fake_pm.saved
    assert saved_slug == "default"
    assert saved_data["agent_api_keys"]["arav-codex-builder"] == "mct_child"


def test_cached_child_api_key_reads_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePm:
        def current(self):
            return "default"

        def get(self, slug):
            assert slug == "default"
            return {"agent_api_keys": {"arav-codex-builder": "mct_child"}}

    monkeypatch.setattr("quorus_cli.codex_agent.ProfileManager", lambda: FakePm())

    assert _cached_child_api_key("arav-codex-builder") == "mct_child"


def test_save_codex_runner_defaults_persists_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = {}

    class FakePm:
        def current(self):
            return "default"

        def get(self, slug):
            assert slug == "default"
            return {"instance_name": "arav-codex"}

        def save(self, slug, data):
            saved["slug"] = slug
            saved["data"] = data

    monkeypatch.setattr("quorus_cli.codex_agent.ProfileManager", lambda: FakePm())

    save_codex_runner_defaults(
        autonomous=True,
        room_poll_seconds=20,
        heartbeat_seconds=45,
        history_limit=40,
        announce=False,
    )

    assert saved["slug"] == "default"
    assert saved["data"]["codex_runner_defaults"] == {
        "autonomous": True,
        "room_poll": 20,
        "heartbeat": 45,
        "history_limit": 40,
        "announce": False,
    }


def test_parent_join_room_uses_parent_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {}

    def fake_request_json(method: str, url: str, **kwargs):
        called["method"] = method
        called["url"] = url
        called["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr("quorus_cli.codex_agent._request_json", fake_request_json)

    parent_join_room(
        relay_url="https://relay.test",
        room="medbuddy-sprint",
        participant="arav-codex-builder",
        parent_api_key="mct_parent",
    )

    assert called["method"] == "POST"
    assert called["url"] == "https://relay.test/rooms/medbuddy-sprint/join"
    assert called["kwargs"]["json_body"] == {"participant": "arav-codex-builder"}
    assert called["kwargs"]["api_key"] == "mct_parent"


def test_send_heartbeat_uses_active_status(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {}

    def fake_request_json(method: str, url: str, **kwargs):
        called["method"] = method
        called["url"] = url
        called["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr("quorus_cli.codex_agent._request_json", fake_request_json)

    send_heartbeat(
        relay_url="https://relay.test",
        room="medbuddy-sprint",
        participant="arav-codex-builder",
        relay_secret="dev-secret",
    )

    assert called["method"] == "POST"
    assert called["url"] == "https://relay.test/heartbeat"
    assert called["kwargs"]["json_body"] == {
        "instance_name": "arav-codex-builder",
        "status": "active",
        "room": "medbuddy-sprint",
    }
    assert called["kwargs"]["relay_secret"] == "dev-secret"


def test_run_codex_agent_tolerates_forbidden_self_join_when_room_is_readable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    response = MagicMock()
    response.status_code = 403
    request = MagicMock()
    exc = httpx.HTTPStatusError("forbidden", request=request, response=response)

    called = {"fetch": 0}

    class DummyThread:
        def __init__(self, target=None, kwargs=None, daemon=None):
            self.target = target
            self.kwargs = kwargs or {}

        def start(self):
            return None

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(
        "quorus_cli.codex_agent.join_room",
        lambda **kwargs: (_ for _ in ()).throw(exc),
    )
    monkeypatch.setattr(
        "quorus_cli.codex_agent.fetch_room_state",
        lambda **kwargs: called.__setitem__("fetch", called["fetch"] + 1) or {},
    )
    monkeypatch.setattr("quorus_cli.codex_agent.threading.Thread", DummyThread)
    monkeypatch.setattr("quorus_cli.codex_agent.run_autonomous_loop", lambda **kwargs: 0)

    rc = run_codex_agent(
        room="medbuddy-sprint",
        relay_url="https://relay.test",
        parent_name="arav-codex",
        parent_api_key="mct_parent",
        relay_secret="",
        requested_name="arav-codex",
        suffix=None,
        cwd=tmp_path,
        wait_seconds=10,
        announce=False,
        no_launch=False,
        verbose=False,
        sandbox="workspace-write",
        approval="on-request",
        autonomous=True,
        room_poll_seconds=180,
        heartbeat_seconds=180,
        history_limit=25,
    )

    assert rc == 0
    assert called["fetch"] == 1


def test_run_codex_agent_saves_defaults_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = {}

    class DummyThread:
        def __init__(self, target=None, kwargs=None, daemon=None):
            self.target = target
            self.kwargs = kwargs or {}

        def start(self):
            return None

        def join(self, timeout=None):
            return None

    monkeypatch.setattr("quorus_cli.codex_agent.join_room", lambda **kwargs: None)
    monkeypatch.setattr("quorus_cli.codex_agent.send_announcement", lambda **kwargs: None)
    monkeypatch.setattr("quorus_cli.codex_agent.threading.Thread", DummyThread)
    monkeypatch.setattr("quorus_cli.codex_agent.run_autonomous_loop", lambda **kwargs: 0)
    monkeypatch.setattr(
        "quorus_cli.codex_agent.save_codex_runner_defaults",
        lambda **kwargs: called.update(kwargs),
    )

    rc = run_codex_agent(
        room="medbuddy-sprint",
        relay_url="https://relay.test",
        parent_name="arav-codex",
        parent_api_key="mct_parent",
        relay_secret="",
        requested_name="arav-codex",
        suffix=None,
        cwd=tmp_path,
        wait_seconds=10,
        announce=True,
        no_launch=False,
        verbose=False,
        sandbox="workspace-write",
        approval="on-request",
        autonomous=True,
        room_poll_seconds=20,
        heartbeat_seconds=45,
        history_limit=40,
        save_defaults=True,
    )

    assert rc == 0
    assert called == {
        "autonomous": True,
        "room_poll_seconds": 20,
        "heartbeat_seconds": 45,
        "history_limit": 40,
        "announce": True,
    }
