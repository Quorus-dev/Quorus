"""``--json`` on the read commands agents script against (0.5.0 sweep, A2).

rooms / history / ps / approvals / state / agent list print the relay's data
as JSON and nothing else on stdout; a relay error still exits non-zero.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import quorus_cli.cli as cli


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "RELAY_URL", "http://test-relay:8080")
    monkeypatch.setattr(cli, "RELAY_SECRET", "test-secret")
    monkeypatch.setattr(cli, "API_KEY", "")
    monkeypatch.setattr(cli, "_cached_jwt", None)
    monkeypatch.setattr(cli, "INSTANCE_NAME", "test-user")


def _client(status: int, payload: object) -> AsyncMock:
    resp = MagicMock()
    resp.status_code = status
    resp.reason_phrase = "Not Found" if status == 404 else "OK"
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=resp,
        )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    return client


def _run(monkeypatch, capsys, argv: list[str], payload: object, status: int = 200) -> str:
    monkeypatch.setattr(cli.sys, "argv", ["quorus", *argv])
    with patch.object(cli, "_get_client", return_value=_client(status, payload)):
        cli.main()
    return capsys.readouterr().out


_ROOMS = [{"id": "r1", "name": "dev", "members": ["alice", "bob"]}]
_MESSAGES = [{"id": "m1", "from_name": "alice", "content": "hi",
              "timestamp": "2026-10-18T00:00:00Z"}]
_AGENTS = [{"name": "arav-claude", "online": True, "status": "idle",
            "last_heartbeat": "2026-10-18T00:00:00+00:00",
            "uptime_start": "2026-10-18T00:00:00+00:00"}]
_STATE = {"active_goal": "ship 0.5.1", "active_agents": ["arav-claude"], "locked_files": {},
          "resolved_decisions": [], "message_count": 3}
_PENDING = [{"id": "apr_1", "agent": "arav-codex", "tool_name": "Bash", "room": "dev",
             "input_preview": "pytest -q"}]


@pytest.mark.parametrize("argv,payload,expected", [
    (["rooms", "--json"], _ROOMS, _ROOMS),
    (["history", "dev", "--json"], _MESSAGES, _MESSAGES),
    (["history", "dev", "--json"], [], []),
    (["ps", "--json"], _AGENTS, _AGENTS),
    (["ps", "--json"], [], []),
    (["state", "dev", "--json"], _STATE, _STATE),
    (["approvals", "--json"], {"pending": _PENDING}, _PENDING),
    (["approvals", "--room", "dev", "--json"], {"pending": []}, []),
])
def test_json_flag_prints_only_the_data(monkeypatch, capsys, argv, payload, expected) -> None:
    out = _run(monkeypatch, capsys, argv, payload)
    assert json.loads(out) == expected


def test_rooms_without_json_still_renders_the_table(monkeypatch, capsys) -> None:
    out = _run(monkeypatch, capsys, ["rooms"], _ROOMS)
    assert "#dev" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


@pytest.mark.parametrize("argv", [
    ["history", "ghost", "--json"],
    ["state", "ghost", "--json"],
])
def test_json_mode_exits_4_when_the_room_is_missing(monkeypatch, capsys, argv) -> None:
    """Human mode prints a hint and exits 0; a script needs the exit code."""
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, capsys, argv, {"detail": "room not found"}, status=404)
    assert exc.value.code == 4


def test_agent_list_json(monkeypatch, capsys, tmp_path) -> None:
    import quorus_cli.agent_cmd as agent_cmd

    (tmp_path / "me-codex.pid").write_text("12345")
    monkeypatch.setattr(agent_cmd, "load_config", lambda: {"instance_name": "me"})
    monkeypatch.setattr(agent_cmd, "_launchd_jobs",
                        lambda: {"com.quorus.agent.me-claude": 1,
                                 "com.quorus.agent.other-claude": 2})
    monkeypatch.setattr(agent_cmd, "_agents_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_cmd.agent_proc, "live_pid", lambda d, name: None)
    monkeypatch.setattr(cli.sys, "argv", ["quorus", "agent", "list", "--json"])
    cli.main()
    assert json.loads(capsys.readouterr().out) == [
        {"name": "me-claude", "running": True, "where": "com.quorus.agent.me-claude"},
        {"name": "me-codex", "running": False, "where": ""},
    ]
