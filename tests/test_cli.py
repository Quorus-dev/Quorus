import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _close_coro(coro):
    """Close a coroutine without awaiting it, preventing RuntimeWarning."""
    if asyncio.iscoroutine(coro):
        coro.close()
    return None


@pytest.fixture(autouse=True)
def configure_cli(monkeypatch):
    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://test-relay:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "test-secret")
    monkeypatch.setattr("quorus.cli.API_KEY", "")
    monkeypatch.setattr("quorus.cli._cached_jwt", None)
    monkeypatch.setattr("quorus.cli.INSTANCE_NAME", "test-user")


def _mock_response(status_code, json_data):
    """Create a sync MagicMock mimicking httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _mock_client(status_code, json_data):
    resp = _mock_response(status_code, json_data)
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    return client


async def test_cli_list_rooms():
    from quorus.cli import _list_rooms

    mock_rooms = [
        {
            "id": "r1",
            "name": "yc-hack",
            "members": ["alice", "bob"],
            "created_at": "2026-04-11T00:00:00Z",
        }
    ]
    with patch("quorus.cli._get_client", return_value=_mock_client(200, mock_rooms)):
        result = await _list_rooms()
    assert len(result) == 1
    assert result[0]["name"] == "yc-hack"


async def test_cli_create_room():
    from quorus.cli import _create_room

    mock_data = {
        "id": "r1",
        "name": "new-room",
        "members": ["test-user"],
        "created_at": "2026-04-11T00:00:00Z",
    }
    with patch("quorus.cli._get_client", return_value=_mock_client(200, mock_data)):
        result = await _create_room("new-room")
    assert result["name"] == "new-room"


async def test_cli_say():
    from quorus.cli import _say

    rooms_data = [{"id": "r1", "name": "yc-hack", "members": ["test-user"]}]
    msg_data = {"id": "m1", "timestamp": "2026-04-11T00:00:00Z"}

    mock_resp_rooms = _mock_response(200, rooms_data)
    mock_resp_msg = _mock_response(200, msg_data)

    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_resp_rooms)
    client.post = AsyncMock(return_value=mock_resp_msg)
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        result = await _say("yc-hack", "hello team")
    assert result["id"] == "m1"


async def test_cli_dm():
    from quorus.cli import _dm

    mock_data = {"id": "m1", "timestamp": "2026-04-11T00:00:00Z"}
    with patch("quorus.cli._get_client", return_value=_mock_client(200, mock_data)):
        result = await _dm("bob", "private message")
    assert result["id"] == "m1"


def test_cli_version(capsys):
    from quorus.cli import _cmd_version

    _cmd_version(MagicMock())
    captured = capsys.readouterr()
    assert "quorus" in captured.out
    assert "0.4.0" in captured.out


def test_cli_logs(capsys):
    from quorus.cli import _cmd_logs

    mock_stats = {
        "total_messages_sent": 100,
        "total_messages_delivered": 80,
        "messages_pending": 5,
        "uptime_seconds": 3661,
        "participants": {
            "alice": {"sent": 50, "received": 40},
            "bob": {"sent": 50, "received": 40},
        },
        "hourly_volume": [{"hour": "2026-04-11T07:00:00", "count": 42}],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_stats
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp):
        _cmd_logs(MagicMock())

    captured = capsys.readouterr()
    assert "100" in captured.out
    assert "alice" in captured.out


def test_cli_logs_relay_down(capsys):
    import httpx

    from quorus.cli import _cmd_logs

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        _cmd_logs(MagicMock())

    captured = capsys.readouterr()
    assert "Cannot connect" in captured.out
    assert "quorus relay" in captured.out


def test_cli_doctor_all_pass(capsys):
    from quorus.cli import _cmd_doctor

    mock_health = MagicMock()
    mock_health.status_code = 200

    mock_rooms = MagicMock()
    mock_rooms.status_code = 200
    mock_rooms.json.return_value = [{"id": "r1", "name": "test"}]

    def mock_get(url, **kwargs):
        if "health" in url:
            return mock_health
        return mock_rooms

    args = MagicMock()
    args.verbose = False

    with patch("httpx.get", side_effect=mock_get), \
         patch("quorus.cli.INSTANCE_NAME", "my-agent"), \
         patch("quorus.cli.RELAY_SECRET", "secret"), \
         patch("quorus.config.resolve_config_file") as mock_resolve:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_resolve.return_value = mock_path
        _cmd_doctor(args)

    captured = capsys.readouterr()
    assert "checks passed" in captured.out


def test_cli_doctor_mcp_registration_detected(capsys, tmp_path):
    """Doctor detects quorus MCP server registration."""
    from quorus.cli import _cmd_doctor

    # Create a mock .claude.json with quorus server
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({
        "mcpServers": {
            "quorus": {
                "command": "uv",
                "args": ["run", "python", "quorus/mcp_server.py"]
            }
        }
    }))

    mock_health = MagicMock()
    mock_health.status_code = 200
    mock_health.json.return_value = {"version": "0.1.0", "uptime_seconds": 100}

    mock_rooms = MagicMock()
    mock_rooms.status_code = 200
    mock_rooms.json.return_value = [{"id": "r1", "name": "test", "members": ["my-agent"]}]

    mock_msgs = MagicMock()
    mock_msgs.status_code = 200
    mock_msgs.json.return_value = {"messages": []}

    def mock_get(url, **kwargs):
        if "health" in url:
            return mock_health
        if "messages" in url:
            return mock_msgs
        return mock_rooms

    args = MagicMock()
    args.verbose = False

    with patch("httpx.get", side_effect=mock_get), \
         patch("quorus.cli.INSTANCE_NAME", "my-agent"), \
         patch("quorus.cli.RELAY_SECRET", "secret"), \
         patch("quorus.cli.Path.home", return_value=tmp_path), \
         patch("quorus.cli.Path.cwd", return_value=tmp_path), \
         patch("quorus.config.resolve_config_file") as mock_resolve:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_resolve.return_value = mock_path
        _cmd_doctor(args)

    captured = capsys.readouterr()
    assert "MCP server registered" in captured.out
    assert "✓" in captured.out  # Check passes when quorus is registered


def test_cli_doctor_mcp_registration_not_found(capsys, tmp_path):
    """Doctor detects missing quorus MCP server registration."""
    from quorus.cli import _cmd_doctor

    # Create a .claude.json without quorus server
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({
        "mcpServers": {
            "other-server": {
                "command": "node",
                "args": ["some-other-server.js"]
            }
        }
    }))

    mock_health = MagicMock()
    mock_health.status_code = 200

    mock_rooms = MagicMock()
    mock_rooms.status_code = 200
    mock_rooms.json.return_value = []

    def mock_get(url, **kwargs):
        if "health" in url:
            return mock_health
        return mock_rooms

    args = MagicMock()
    args.verbose = False

    with patch("httpx.get", side_effect=mock_get), \
         patch("quorus.cli.INSTANCE_NAME", "my-agent"), \
         patch("quorus.cli.RELAY_SECRET", "secret"), \
         patch("quorus.cli.Path.home", return_value=tmp_path), \
         patch("quorus.cli.Path.cwd", return_value=tmp_path), \
         patch("quorus.config.resolve_config_file") as mock_resolve:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_resolve.return_value = mock_path
        _cmd_doctor(args)

    captured = capsys.readouterr()
    assert "MCP server registered" in captured.out
    assert "✗" in captured.out


# ── export command tests ────────────────────────────────────────────────

_SAMPLE_MSGS = [
    {
        "id": "m1",
        "from_name": "alice",
        "room": "dev",
        "content": "hello world",
        "message_type": "chat",
        "timestamp": "2026-04-11T08:00:00Z",
    },
    {
        "id": "m2",
        "from_name": "bob",
        "room": "dev",
        "content": "claiming task",
        "message_type": "claim",
        "timestamp": "2026-04-11T08:01:00Z",
    },
]


async def test_export_json_stdout(capsys):
    from quorus.cli import _export

    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("quorus.cli._get_client", return_value=client):
        await _export("dev", fmt="json")

    captured = capsys.readouterr()
    import json
    data = json.loads(captured.out)
    assert len(data) == 2
    assert data[0]["from_name"] == "alice"


async def test_export_md_stdout(capsys):
    from quorus.cli import _export

    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("quorus.cli._get_client", return_value=client):
        await _export("dev", fmt="md")

    captured = capsys.readouterr()
    assert "# Room: dev" in captured.out
    assert "**alice**" in captured.out
    assert "**[claim]**" in captured.out


async def test_export_json_to_file(tmp_path):
    from quorus.cli import _export

    out_file = str(tmp_path / "export.json")
    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("quorus.cli._get_client", return_value=client):
        await _export("dev", fmt="json", output=out_file)

    import json
    data = json.loads((tmp_path / "export.json").read_text())
    assert len(data) == 2


async def test_export_md_to_file(tmp_path):
    from quorus.cli import _export

    out_file = str(tmp_path / "export.md")
    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("quorus.cli._get_client", return_value=client):
        await _export("dev", fmt="md", output=out_file)

    content = (tmp_path / "export.md").read_text()
    assert "# Room: dev" in content
    assert "**bob**" in content


async def test_export_empty_room(capsys):
    from quorus.cli import _export

    client = _mock_client(200, [])
    with patch("quorus.cli._get_client", return_value=client):
        await _export("empty-room", fmt="json")

    captured = capsys.readouterr()
    assert "No messages" in captured.out


async def test_export_room_not_found(capsys):
    import httpx

    from quorus.cli import _export

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    with patch("quorus.cli._get_client", return_value=client):
        await _export("ghost-room", fmt="json")

    captured = capsys.readouterr()
    assert "not found" in captured.out


async def test_export_unknown_format(capsys):
    from quorus.cli import _export

    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("quorus.cli._get_client", return_value=client):
        await _export("dev", fmt="csv")

    captured = capsys.readouterr()
    assert "Unknown format" in captured.out


# ── add-agent wizard tests ──────────────────────────────────────────────

def test_add_agent_creates_workspace(tmp_path, monkeypatch):
    from quorus.cli import _cmd_add_agent

    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://test:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "test-secret")

    # Redirect workspace to tmp_path
    monkeypatch.setattr("quorus.cli.Path.home", lambda: tmp_path)

    # Mock rich prompts to provide answers
    call_count = {"n": 0}
    answers = ["test-bot", "dev", "sonnet", "high"]

    def mock_ask(prompt, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(answers):
            return answers[idx]
        return kwargs.get("default", "")

    def mock_confirm(prompt, **kwargs):
        return True

    # Mock _list_rooms and _auto_join
    mock_rooms = [{"name": "dev", "id": "r1", "members": []}]
    _mock_client(200, {})

    return_values = iter([mock_rooms, None])

    def _close_coro_with_return(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return next(return_values)

    with patch("quorus.cli.Prompt.ask", side_effect=mock_ask), \
         patch("quorus.cli.Confirm.ask", side_effect=mock_confirm), \
         patch("quorus.cli.asyncio.run", side_effect=_close_coro_with_return), \
         patch("quorus.cli.subprocess.run"), \
         patch("quorus.cli.sys.platform", "darwin"):
        _cmd_add_agent(MagicMock())

    workspace = tmp_path / "quorus-agents" / "test-bot"
    assert workspace.exists()
    assert (workspace / ".mcp.json").exists()
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "settings.json").exists()

    import json
    claude_md = (workspace / "CLAUDE.md").read_text()
    assert "test-bot" in claude_md
    assert "dev" in claude_md

    mcp = json.loads((workspace / ".mcp.json").read_text())
    assert mcp["mcpServers"]["quorus"]["env"]["INSTANCE_NAME"] == "test-bot"


# ── connect command tests ────────────────────────────────────────────────

def test_connect_codex(capsys):
    from quorus.cli import _cmd_connect

    args = MagicMock()
    args.platform = "codex"
    args.room = "dev"
    args.name = "codex-bot"

    client = _mock_client(200, {})
    with patch("quorus.cli._get_client", return_value=client), \
         patch("quorus.cli.asyncio.run", side_effect=_close_coro):
        _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Codex Agent Setup" in captured.out
    assert "codex-bot" in captured.out
    assert "/messages/codex-bot" in captured.out


def test_connect_cursor(capsys):
    from quorus.cli import _cmd_connect

    args = MagicMock()
    args.platform = "cursor"
    args.room = "dev"
    args.name = "cursor-bot"

    client = _mock_client(200, {})
    with patch("quorus.cli._get_client", return_value=client), \
         patch("quorus.cli.asyncio.run", side_effect=_close_coro):
        _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Cursor Agent Setup" in captured.out
    assert "mcpServers" in captured.out
    assert "cursor-bot" in captured.out


def test_connect_ollama(capsys):
    from quorus.cli import _cmd_connect

    args = MagicMock()
    args.platform = "ollama"
    args.room = "dev"
    args.name = "llama-bot"

    client = _mock_client(200, {})
    with patch("quorus.cli._get_client", return_value=client), \
         patch("quorus.cli.asyncio.run", side_effect=_close_coro):
        _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Ollama Agent Setup" in captured.out
    assert "llama-bot" in captured.out
    assert "ollama_agent.py" in captured.out


def test_connect_claude(capsys):
    from quorus.cli import _cmd_connect

    args = MagicMock()
    args.platform = "claude"
    args.room = "dev"
    args.name = "claude-bot"

    client = _mock_client(200, {})
    with patch("quorus.cli._get_client", return_value=client), \
         patch("quorus.cli.asyncio.run", side_effect=_close_coro):
        _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Claude Code Agent Setup" in captured.out
    assert "quorus add-agent" in captured.out


# ── search command tests ─────────────────────────────────────────────────

async def test_search_by_keyword(capsys):
    from quorus.cli import _search

    results = [_SAMPLE_MSGS[0]]  # only "hello world"
    client = _mock_client(200, results)
    with patch("quorus.cli._get_client", return_value=client):
        await _search("dev", query="hello")

    captured = capsys.readouterr()
    assert "alice" in captured.out
    assert "1 matches" in captured.out


async def test_search_by_sender(capsys):
    from quorus.cli import _search

    results = [_SAMPLE_MSGS[1]]  # only bob
    client = _mock_client(200, results)
    with patch("quorus.cli._get_client", return_value=client):
        await _search("dev", sender="bob")

    captured = capsys.readouterr()
    assert "bob" in captured.out


async def test_search_by_message_type(capsys):
    from quorus.cli import _search

    results = [_SAMPLE_MSGS[1]]  # claim type
    client = _mock_client(200, results)
    with patch("quorus.cli._get_client", return_value=client):
        await _search("dev", message_type="claim")

    captured = capsys.readouterr()
    assert "claim" in captured.out


async def test_search_no_results(capsys):
    from quorus.cli import _search

    client = _mock_client(200, [])
    with patch("quorus.cli._get_client", return_value=client):
        await _search("dev", query="nonexistent")

    captured = capsys.readouterr()
    assert "No matching" in captured.out


async def test_search_room_not_found(capsys):
    import httpx

    from quorus.cli import _search

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    with patch("quorus.cli._get_client", return_value=client):
        await _search("ghost", query="test")

    captured = capsys.readouterr()
    assert "not found" in captured.out


# ── metrics command tests ────────────────────────────────────────────────

_METRICS_MSGS = [
    {
        "from_name": "alice", "content": "hello", "message_type": "chat",
        "timestamp": "2026-04-11T08:00:00Z",
    },
    {
        "from_name": "bob", "content": "CLAIM: auth", "message_type": "claim",
        "timestamp": "2026-04-11T08:01:00Z",
    },
    {
        "from_name": "bob", "content": "STATUS: auth complete", "message_type": "status",
        "timestamp": "2026-04-11T08:30:00Z",
    },
    {
        "from_name": "alice", "content": "nice work", "message_type": "chat",
        "timestamp": "2026-04-11T09:00:00Z",
    },
]

_ANALYTICS_DATA = {
    "total_messages_sent": 100,
    "total_messages_delivered": 95,
    "messages_pending": 5,
    "participants": {"alice": {"sent": 50, "received": 45}},
    "hourly_volume": [],
    "uptime_seconds": 3661,
}


async def test_metrics_shows_agent_activity(capsys):
    from quorus.cli import _metrics

    hist_resp = MagicMock()
    hist_resp.status_code = 200
    hist_resp.json.return_value = _METRICS_MSGS
    hist_resp.raise_for_status = MagicMock()

    analytics_resp = MagicMock()
    analytics_resp.status_code = 200
    analytics_resp.json.return_value = _ANALYTICS_DATA
    analytics_resp.raise_for_status = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[hist_resp, analytics_resp])
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _metrics("dev")

    captured = capsys.readouterr()
    assert "Agent Activity" in captured.out
    assert "alice" in captured.out
    assert "bob" in captured.out
    assert "Message Types" in captured.out
    assert "Relay Summary" in captured.out
    assert "100" in captured.out  # total sent


async def test_metrics_empty_room(capsys):
    from quorus.cli import _metrics

    hist_resp = MagicMock()
    hist_resp.status_code = 200
    hist_resp.json.return_value = []
    hist_resp.raise_for_status = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(return_value=hist_resp)
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _metrics("empty")

    captured = capsys.readouterr()
    assert "No messages" in captured.out


async def test_metrics_room_not_found(capsys):
    import httpx

    from quorus.cli import _metrics

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _metrics("ghost")

    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_connect_unknown_platform(capsys):
    from quorus.cli import _cmd_connect

    args = MagicMock()
    args.platform = "gpt"
    args.room = "dev"
    args.name = "gpt-bot"

    _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Unknown platform" in captured.out


def test_add_agent_cancelled(monkeypatch, capsys):
    from quorus.cli import _cmd_add_agent

    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://test:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "test-secret")

    answers = ["cancel-bot", "dev", "sonnet", "high"]
    call_count = {"n": 0}

    def mock_ask(prompt, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(answers):
            return answers[idx]
        return kwargs.get("default", "")

    confirm_count = {"n": 0}

    def mock_confirm(prompt, **kwargs):
        confirm_count["n"] += 1
        # First confirm (Launch agent?) — say no
        return False

    def _close_coro_return_empty(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return []

    with patch("quorus.cli.Prompt.ask", side_effect=mock_ask), \
         patch("quorus.cli.Confirm.ask", side_effect=mock_confirm), \
         patch("quorus.cli.asyncio.run", side_effect=_close_coro_return_empty):
        _cmd_add_agent(MagicMock())

    captured = capsys.readouterr()
    assert "Cancelled" in captured.out


# ── state command tests ──────────────────────────────────────────────────────

_SAMPLE_STATE = {
    "room_id": "r1",
    "snapshot_at": "2026-04-11T10:00:00Z",
    "schema_version": "1.0",
    "active_goal": "Build distributed mutex locking layer",
    "claimed_tasks": [],
    "locked_files": {
        "quorus/relay.py": {
            "held_by": "arav-agent-1",
            "claimed_by": "arav-agent-1",
            "expires_at": "2026-04-11T10:05:00Z",
            "lock_token": "abc12345-token",
        },
        "quorus/mcp.py": {
            "held_by": "arav-agent-2",
            "claimed_by": "arav-agent-2",
            "expires_at": "2026-04-11T10:02:00Z",
            "lock_token": "def67890-token",
        },
    },
    "resolved_decisions": ["use redis", "use fastapi", "add SSE"],
    "active_agents": ["arav", "arav-agent-1", "arav-agent-2"],
    "message_count": 47,
    "last_activity": "2026-04-11T09:58:00Z",
}


async def test_cmd_room_state_shows_goal(capsys):
    from quorus.cli import _room_state

    client = _mock_client(200, _SAMPLE_STATE)
    with patch("quorus.cli._get_client", return_value=client):
        await _room_state("quorus-dev")

    captured = capsys.readouterr()
    assert "Build distributed mutex locking layer" in captured.out
    assert "arav-agent-1" in captured.out
    assert "3 online" in captured.out
    assert "quorus/relay.py" in captured.out
    assert "47" in captured.out


async def test_cmd_room_state_no_goal(capsys):
    from quorus.cli import _room_state

    state = dict(_SAMPLE_STATE)
    state["active_goal"] = None
    state["locked_files"] = {}
    client = _mock_client(200, state)
    with patch("quorus.cli._get_client", return_value=client):
        await _room_state("quorus-dev")

    captured = capsys.readouterr()
    assert "No active goal" in captured.out
    assert "None" in captured.out


async def test_cmd_room_state_not_found(capsys):
    import httpx

    from quorus.cli import _room_state

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    with patch("quorus.cli._get_client", return_value=client):
        await _room_state("ghost-room")

    captured = capsys.readouterr()
    assert "not found" in captured.out


# ── locks command tests ──────────────────────────────────────────────────────


async def test_cmd_room_locks_shows_table(capsys):
    from quorus.cli import _room_locks

    client = _mock_client(200, _SAMPLE_STATE)
    with patch("quorus.cli._get_client", return_value=client):
        await _room_locks("quorus-dev")

    captured = capsys.readouterr()
    assert "quorus/relay.py" in captured.out
    assert "arav-agent-1" in captured.out
    assert "abc12345" in captured.out


async def test_cmd_room_locks_empty(capsys):
    from quorus.cli import _room_locks

    state = dict(_SAMPLE_STATE)
    state["locked_files"] = {}
    client = _mock_client(200, state)
    with patch("quorus.cli._get_client", return_value=client):
        await _room_locks("quorus-dev")

    captured = capsys.readouterr()
    assert "No active locks" in captured.out


# ── usage command tests ──────────────────────────────────────────────────────

_ANALYTICS_USAGE = {
    "total_messages_sent": 1247,
    "total_messages_delivered": 1200,
    "messages_pending": 3,
    "participants": {
        "arav-agent-1": {"sent": 342, "received": 300},
        "arav-agent-2": {"sent": 218, "received": 200},
        "arav": {"sent": 156, "received": 140},
    },
    "hourly_volume": [],
    "uptime_seconds": 7200,
}

_ROOMS_LIST = [
    {"id": "r1", "name": "quorus-dev", "members": ["arav", "arav-agent-1"]},
    {"id": "r2", "name": "quorus-prod", "members": ["arav-agent-2"]},
    {"id": "r3", "name": "staging", "members": []},
]

_PRESENCE_LIST = [
    {"name": "arav", "online": True},
    {"name": "arav-agent-1", "online": True},
    {"name": "arav-agent-2", "online": False},
]


async def test_cmd_usage_shows_stats(capsys):
    from quorus.cli import _usage

    analytics_resp = _mock_response(200, _ANALYTICS_USAGE)
    analytics_resp.is_success = True
    rooms_resp = _mock_response(200, _ROOMS_LIST)
    rooms_resp.is_success = True
    presence_resp = _mock_response(200, _PRESENCE_LIST)
    presence_resp.is_success = True

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[analytics_resp, rooms_resp, presence_resp])
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _usage()

    captured = capsys.readouterr()
    assert "1,247" in captured.out
    assert "arav-agent-1" in captured.out
    assert "342" in captured.out


async def test_cmd_usage_relay_down(capsys):
    import httpx

    from quorus.cli import _usage

    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _usage()

    captured = capsys.readouterr()
    assert "Cannot connect" in captured.out


# -----------------------------------------------------------------------------
# inbox / hook command tests
# -----------------------------------------------------------------------------


def test_cmd_inbox_no_messages(capsys):
    """When no messages, inbox should exit silently."""
    import httpx as httpx_mod

    from quorus.cli import _cmd_inbox

    peek_resp = MagicMock()
    peek_resp.status_code = 200
    peek_resp.json.return_value = {"count": 0, "pending": 0}
    peek_resp.raise_for_status = MagicMock()

    with patch.object(httpx_mod, "get", return_value=peek_resp):
        args = MagicMock()
        args.quiet = False
        args.json = False
        _cmd_inbox(args)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_cmd_inbox_with_messages(capsys):
    """When messages exist, inbox should print them."""
    import httpx as httpx_mod

    from quorus.cli import _cmd_inbox

    peek_resp = MagicMock()
    peek_resp.status_code = 200
    peek_resp.json.return_value = {"count": 2, "pending": 0}
    peek_resp.raise_for_status = MagicMock()

    fetch_resp = MagicMock()
    fetch_resp.status_code = 200
    fetch_resp.json.return_value = {
        "messages": [
            {"from_name": "alice", "content": "Hello", "timestamp": "2026-04-12T12:34:00Z"},
            {"from_name": "bob", "content": "World", "timestamp": "2026-04-12T12:35:00Z"},
        ],
        "ack_token": "tok123",
    }
    fetch_resp.raise_for_status = MagicMock()

    with patch.object(httpx_mod, "get", side_effect=[peek_resp, fetch_resp]):
        args = MagicMock()
        args.quiet = False
        args.json = False
        _cmd_inbox(args)

    captured = capsys.readouterr()
    assert "[quorus]" in captured.out
    assert "2 new messages" in captured.out
    assert "alice" in captured.out
    assert "bob" in captured.out


def test_cmd_inbox_json_output(capsys):
    """--json flag should output raw JSON."""
    import httpx as httpx_mod

    from quorus.cli import _cmd_inbox

    peek_resp = MagicMock()
    peek_resp.status_code = 200
    peek_resp.json.return_value = {"count": 1, "pending": 0}
    peek_resp.raise_for_status = MagicMock()

    messages = [{"from_name": "alice", "content": "Test", "timestamp": "2026-04-12T12:34:00Z"}]
    fetch_resp = MagicMock()
    fetch_resp.status_code = 200
    fetch_resp.json.return_value = {"messages": messages, "ack_token": "tok"}
    fetch_resp.raise_for_status = MagicMock()

    with patch.object(httpx_mod, "get", side_effect=[peek_resp, fetch_resp]):
        args = MagicMock()
        args.quiet = False
        args.json = True
        _cmd_inbox(args)

    captured = capsys.readouterr()
    import json
    output = json.loads(captured.out)
    assert output[0]["from_name"] == "alice"


def test_cmd_inbox_relay_unreachable(capsys):
    """Should exit silently when relay is unreachable."""
    import httpx as httpx_mod

    from quorus.cli import _cmd_inbox

    with patch.object(httpx_mod, "get", side_effect=httpx_mod.ConnectError("refused")):
        args = MagicMock()
        args.quiet = False
        args.json = False
        _cmd_inbox(args)

    captured = capsys.readouterr()
    assert captured.out == ""  # Silent exit


def test_cmd_hook_status_not_configured(capsys, tmp_path):
    """Hook status should show not configured when no hook exists."""
    from quorus.cli import _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    with patch("quorus.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "status"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "not configured" in captured.out


def test_cmd_hook_enable_creates_hook(capsys, tmp_path):
    """Hook enable should create the settings file with hook config."""
    import json

    from quorus.cli import _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    with patch("quorus.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "enable"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "enabled" in captured.out

    # Verify file was created
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    assert "UserPromptSubmit" in settings["hooks"]
    hooks = settings["hooks"]["UserPromptSubmit"]
    assert any("quorus inbox" in str(h) for h in hooks)


def test_cmd_hook_enable_already_enabled(capsys, tmp_path):
    """Hook enable when already enabled should show warning."""
    import json

    from quorus.cli import QUORUS_HOOK_CONFIG, _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [QUORUS_HOOK_CONFIG]}
    }))

    with patch("quorus.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "enable"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "already enabled" in captured.out


def test_cmd_hook_disable_removes_hook(capsys, tmp_path):
    """Hook disable should remove the quorus hook from settings."""
    import json

    from quorus.cli import QUORUS_HOOK_CONFIG, _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [QUORUS_HOOK_CONFIG]}
    }))

    with patch("quorus.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "disable"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "disabled" in captured.out

    # Verify hook was removed
    settings = json.loads(settings_path.read_text())
    hooks = settings["hooks"]["UserPromptSubmit"]
    assert not any("quorus inbox" in str(h) for h in hooks)


def test_cmd_hook_status_when_enabled(capsys, tmp_path):
    """Hook status should show enabled when hook exists."""
    import json

    from quorus.cli import QUORUS_HOOK_CONFIG, _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [QUORUS_HOOK_CONFIG]}
    }))

    with patch("quorus.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "status"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "enabled" in captured.out


# ── context command (Summary Cascade v1) tests ───────────────────────────

_CONTEXT_STATE = {
    "room_id": "r1",
    "active_goal": "Build the auth module",
    "claimed_tasks": [
        {
            "id": "t1",
            "claimed_by": "agent-1",
            "description": "implement JWT middleware",
            "file_path": "src/auth/middleware.py",
        }
    ],
    "locked_files": {
        "src/auth/middleware.py": {
            "held_by": "agent-1",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
    },
    "resolved_decisions": [
        {
            "id": "d1",
            "decision": "Use RS256 for JWT signing",
            "decided_at": "2026-04-11T08:00:00Z",
        }
    ],
    "active_agents": ["agent-1", "agent-2"],
    "message_count": 5,
    "last_activity": "2026-04-11T09:00:00Z",
}

_CONTEXT_HISTORY = [
    {
        "id": "m1",
        "from_name": "alice",
        "content": "Build the auth module",
        "message_type": "brief",
        "brief_id": "brief-uuid-1234",
        "timestamp": "2026-04-11T07:00:00Z",
    },
    {
        "id": "m2",
        "from_name": "agent-1",
        "content": "Claiming JWT middleware implementation",
        "message_type": "claim",
        "timestamp": "2026-04-11T07:30:00Z",
    },
    {
        "id": "m3",
        "from_name": "agent-1",
        "content": "Use RS256 for JWT signing",
        "message_type": "decision",
        "timestamp": "2026-04-11T08:00:00Z",
    },
    {
        "id": "m4",
        "from_name": "agent-1",
        "content": "JWT middleware done, pushing to branch feat/jwt",
        "message_type": "status",
        "timestamp": "2026-04-11T08:30:00Z",
    },
    {
        "id": "m5",
        "from_name": "alice",
        "content": "just chatting",
        "message_type": "chat",
        "timestamp": "2026-04-11T09:00:00Z",
    },
]


def _mock_context_client():
    """Client whose parallel gather calls return state + history."""
    state_resp = MagicMock()
    state_resp.status_code = 200
    state_resp.json.return_value = _CONTEXT_STATE
    state_resp.raise_for_status = MagicMock()

    hist_resp = MagicMock()
    hist_resp.status_code = 200
    hist_resp.json.return_value = _CONTEXT_HISTORY
    hist_resp.raise_for_status = MagicMock()

    client = AsyncMock()
    # asyncio.gather calls both get() coroutines; side_effect feeds them in order
    client.get = AsyncMock(side_effect=[state_resp, hist_resp])
    client.aclose = AsyncMock()
    return client


async def test_context_shows_active_goal(capsys):
    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev")

    captured = capsys.readouterr()
    assert "Build the auth module" in captured.out
    assert "Active Goal" in captured.out


async def test_context_shows_header_without_quiet(capsys):
    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev", quiet=False)

    captured = capsys.readouterr()
    assert "=== Room Context: dev ===" in captured.out


async def test_context_quiet_suppresses_header(capsys):
    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev", quiet=True)

    captured = capsys.readouterr()
    assert "=== Room Context" not in captured.out
    # Content should still be present
    assert "Build the auth module" in captured.out


async def test_context_shows_claimed_tasks(capsys):
    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev")

    captured = capsys.readouterr()
    assert "agent-1" in captured.out
    assert "implement JWT middleware" in captured.out


async def test_context_shows_locked_files(capsys):
    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev")

    captured = capsys.readouterr()
    assert "src/auth/middleware.py" in captured.out


async def test_context_shows_decisions(capsys):
    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev")

    captured = capsys.readouterr()
    assert "RS256" in captured.out


async def test_context_shows_status_updates(capsys):
    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev")

    captured = capsys.readouterr()
    assert "JWT middleware done" in captured.out


async def test_context_filters_chat_messages(capsys):
    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev")

    captured = capsys.readouterr()
    # "just chatting" is a chat-type message; should be excluded from context
    assert "just chatting" not in captured.out


async def test_context_json_output(capsys):
    import json as _json

    from quorus.cli import _context

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev", json_output=True)

    captured = capsys.readouterr()
    data = _json.loads(captured.out)
    assert data["room"] == "dev"
    assert data["active_goal"] == "Build the auth module"
    assert isinstance(data["claimed_tasks"], list)
    assert isinstance(data["resolved_decisions"], list)


async def test_context_deduplicates_messages(capsys):
    from quorus.cli import _context

    # Two identical status messages — should appear only once
    duplicate_history = [
        {
            "id": "m1",
            "from_name": "agent-1",
            "content": "same status",
            "message_type": "status",
            "timestamp": "2026-04-11T08:00:00Z",
        },
        {
            "id": "m2",
            "from_name": "agent-1",
            "content": "same status",
            "message_type": "status",
            "timestamp": "2026-04-11T08:01:00Z",
        },
    ]
    state_resp = MagicMock()
    state_resp.status_code = 200
    state_resp.json.return_value = {**_CONTEXT_STATE, "claimed_tasks": [], "locked_files": {}}
    state_resp.raise_for_status = MagicMock()
    hist_resp = MagicMock()
    hist_resp.status_code = 200
    hist_resp.json.return_value = duplicate_history
    hist_resp.raise_for_status = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[state_resp, hist_resp])
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _context("dev")

    captured = capsys.readouterr()
    # "same status" should appear exactly once
    assert captured.out.count("same status") == 1


async def test_context_relay_unreachable(capsys):
    import httpx

    from quorus.cli import _context

    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _context("dev")

    captured = capsys.readouterr()
    assert "Cannot connect" in captured.out


async def test_context_quiet_suppresses_relay_error(capsys):
    import httpx

    from quorus.cli import _context

    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _context("dev", quiet=True)

    captured = capsys.readouterr()
    assert captured.out.strip() == ""


async def test_context_room_not_found(capsys):
    import httpx

    from quorus.cli import _context

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _context("ghost-room")

    captured = capsys.readouterr()
    assert "not found" in captured.out


async def test_context_auto_detects_room(capsys):
    from quorus.cli import _context

    rooms_resp = MagicMock()
    rooms_resp.status_code = 200
    rooms_resp.json.return_value = [
        {"id": "r1", "name": "dev", "members": ["test-user"]},
    ]
    rooms_resp.raise_for_status = MagicMock()

    state_resp = MagicMock()
    state_resp.status_code = 200
    state_resp.json.return_value = _CONTEXT_STATE
    state_resp.raise_for_status = MagicMock()

    hist_resp = MagicMock()
    hist_resp.status_code = 200
    hist_resp.json.return_value = _CONTEXT_HISTORY
    hist_resp.raise_for_status = MagicMock()

    client = AsyncMock()
    # First call: list rooms; second + third: state and history (gathered)
    client.get = AsyncMock(side_effect=[rooms_resp, state_resp, hist_resp])
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _context(room_name=None)

    captured = capsys.readouterr()
    assert "Build the auth module" in captured.out


async def test_context_summarize_requires_api_key(capsys, monkeypatch):
    """context --summarize should fail gracefully without ANTHROPIC_API_KEY."""
    from quorus.cli import _context

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("quorus.cli._get_client", return_value=_mock_context_client()):
        await _context("dev", summarize=True)

    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY not set" in captured.out


async def test_context_summarize_calls_llm(capsys, monkeypatch):
    """context --summarize should call Claude and print the summary."""
    from quorus.cli import _context

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # Mock the anthropic client
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="This is a test summary.")]

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages.create.return_value = mock_response

    # Create a mock httpx client that returns the right data
    http_client = _mock_context_client()
    # Ensure aclose is an AsyncMock
    http_client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=http_client):
        # Patch anthropic at the import point in the function
        with patch.dict("sys.modules", {"anthropic": MagicMock(
            Anthropic=MagicMock(return_value=mock_anthropic_client)
        )}):
            await _context("dev", summarize=True)

    captured = capsys.readouterr()
    assert "This is a test summary" in captured.out


# ── decision command tests ────────────────────────────────────────────────


def test_decision_posts_and_prints_confirmation(capsys):
    from quorus.cli import _cmd_decision

    result = {
        "id": "dec-uuid-1234",
        "decision": "Use RS256 for JWT signing",
        "decided_by": "test-user",
        "decided_at": "2026-04-11T08:00:00Z",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = result
    mock_resp.raise_for_status = MagicMock()

    args = MagicMock()
    args.room = "dev"
    args.decision = ["Use", "RS256", "for", "JWT", "signing"]

    with patch("httpx.post", return_value=mock_resp):
        _cmd_decision(args)

    captured = capsys.readouterr()
    assert "Decision recorded" in captured.out
    assert "RS256" in captured.out
    assert "dev" in captured.out


def test_decision_shows_id_prefix(capsys):
    from quorus.cli import _cmd_decision

    result = {
        "id": "abcdef12-rest-of-uuid",
        "decision": "Use Postgres for persistence",
        "decided_by": "test-user",
        "decided_at": "2026-04-11T09:00:00Z",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = result
    mock_resp.raise_for_status = MagicMock()

    args = MagicMock()
    args.room = "dev"
    args.decision = ["Use", "Postgres", "for", "persistence"]

    with patch("httpx.post", return_value=mock_resp):
        _cmd_decision(args)

    captured = capsys.readouterr()
    # ID should be truncated to 8 chars
    assert "abcdef12" in captured.out


def test_decision_empty_text_rejected(capsys):
    from quorus.cli import _cmd_decision

    args = MagicMock()
    args.room = "dev"
    args.decision = ["   "]

    _cmd_decision(args)

    captured = capsys.readouterr()
    assert "empty" in captured.out.lower()


def test_decision_relay_unreachable(capsys):
    import httpx

    from quorus.cli import _cmd_decision

    args = MagicMock()
    args.room = "dev"
    args.decision = ["Use RS256"]

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        _cmd_decision(args)

    captured = capsys.readouterr()
    assert "Cannot connect" in captured.out


def test_decision_room_not_found(capsys):
    import httpx

    from quorus.cli import _cmd_decision

    resp = MagicMock()
    resp.status_code = 404
    resp.text = "Not Found"
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )

    args = MagicMock()
    args.room = "ghost"
    args.decision = ["Use RS256"]

    with patch("httpx.post", return_value=resp):
        _cmd_decision(args)

    captured = capsys.readouterr()
    assert "not found" in captured.out


# ── hook command includes context in combined command ─────────────────────


def test_hook_command_includes_both_inbox_and_context():
    """Enabled hook should run both inbox and context for auto-injection."""
    from quorus.cli import QUORUS_HOOK_CONFIG

    all_commands = " ".join(h["command"] for h in QUORUS_HOOK_CONFIG["hooks"])
    assert "quorus inbox" in all_commands
    assert "quorus context" in all_commands
    assert "--quiet" in all_commands


# ── resolve command (CRA - Conflict Resolution Agent) ────────────────────────


def test_resolve_conflict_pattern_parsing():
    """Verify conflict marker regex extracts conflict blocks correctly."""
    import re

    conflict_pattern = re.compile(
        r"(<{7} .+?\n[\s\S]*?={7}\n[\s\S]*?>{7} .+?\n)",
        re.MULTILINE,
    )

    sample_file = """\
def hello():
<<<<<<< HEAD
    print("Hello from main")
=======
    print("Hello from feature")
>>>>>>> feature-branch
    return True
"""
    conflicts = conflict_pattern.findall(sample_file)
    assert len(conflicts) == 1
    assert "HEAD" in conflicts[0]
    assert "feature-branch" in conflicts[0]
    assert 'print("Hello from main")' in conflicts[0]
    assert 'print("Hello from feature")' in conflicts[0]


def test_resolve_multiple_conflicts():
    """Verify regex handles multiple conflict blocks in one file."""
    import re

    conflict_pattern = re.compile(
        r"(<{7} .+?\n[\s\S]*?={7}\n[\s\S]*?>{7} .+?\n)",
        re.MULTILINE,
    )

    sample_file = """\
<<<<<<< HEAD
import foo
=======
import bar
>>>>>>> branch-a
class MyClass:
<<<<<<< HEAD
    x = 1
=======
    x = 2
>>>>>>> branch-a
"""
    conflicts = conflict_pattern.findall(sample_file)
    assert len(conflicts) == 2


def test_resolve_no_conflicts_clean_exit(capsys, monkeypatch, tmp_path):
    """resolve should exit cleanly when no conflicts are found."""
    import subprocess
    from unittest.mock import MagicMock

    from quorus.cli import _cmd_resolve

    # Mock git to return no conflicted files
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    args = MagicMock()
    args.room = None
    args.model = "claude-sonnet-4-6"

    _cmd_resolve(args)

    captured = capsys.readouterr()
    assert "No merge conflicts found" in captured.out


def test_resolve_empty_conflict_block():
    """Regex should handle edge case of empty conflict sides gracefully."""
    import re

    conflict_pattern = re.compile(
        r"(<{7} .+?\n[\s\S]*?={7}\n[\s\S]*?>{7} .+?\n)",
        re.MULTILINE,
    )

    # Edge case: empty ours side
    sample_empty_ours = """\
<<<<<<< HEAD
=======
    print("only in theirs")
>>>>>>> feature
"""
    conflicts = conflict_pattern.findall(sample_empty_ours)
    assert len(conflicts) == 1

    # Edge case: empty theirs side
    sample_empty_theirs = """\
<<<<<<< HEAD
    print("only in ours")
=======
>>>>>>> feature
"""
    conflicts = conflict_pattern.findall(sample_empty_theirs)
    assert len(conflicts) == 1

    # Edge case: both sides empty (deletion conflict)
    sample_both_empty = """\
<<<<<<< HEAD
=======
>>>>>>> feature
"""
    conflicts = conflict_pattern.findall(sample_both_empty)
    assert len(conflicts) == 1


def test_resolve_no_api_key_error(capsys, monkeypatch):
    """resolve should error gracefully when ANTHROPIC_API_KEY is not set."""
    import subprocess
    from unittest.mock import MagicMock

    from quorus.cli import _cmd_resolve

    # Mock git to return conflicted files
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "src/auth.py"

    def mock_run(*a, **kw):
        return mock_result

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    args = MagicMock()
    args.room = None
    args.model = "claude-sonnet-4-6"

    try:
        _cmd_resolve(args)
    except SystemExit:
        pass  # Expected when API key is missing

    captured = capsys.readouterr()
    # Should mention API key or conflicts
    assert len(captured.out) > 0


def test_resolve_git_command_failure(capsys, monkeypatch):
    """resolve should handle git command failures gracefully."""
    import subprocess
    from unittest.mock import MagicMock

    from quorus.cli import _cmd_resolve

    # Mock git to fail
    mock_result = MagicMock()
    mock_result.returncode = 128
    mock_result.stdout = ""
    mock_result.stderr = "fatal: not a git repository"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    args = MagicMock()
    args.room = None
    args.model = "claude-sonnet-4-6"

    try:
        _cmd_resolve(args)
    except SystemExit as e:
        assert e.code == 1  # Expected exit code for git failure

    captured = capsys.readouterr()
    assert "git diff failed" in captured.out or "not a git repository" in captured.out


# ---------------------------------------------------------------------------
# _cmd_init tests
# ---------------------------------------------------------------------------


def _make_init_args(
    name="agent-3",
    relay_url="http://localhost:8080",
    secret="s3cr3t",
    api_key=None,
):
    args = MagicMock()
    args.name = name
    args.relay_url = relay_url
    args.secret = secret
    args.api_key = api_key
    return args


def test_cmd_init_happy_path(tmp_path, monkeypatch):
    """Init writes config, registers MCP into ~/.claude.json, and prints a success summary."""
    from quorus.cli import _cmd_init

    monkeypatch.setattr("quorus.cli.Path.home", lambda: tmp_path)
    # uv available
    monkeypatch.setattr("quorus.cli.shutil.which", lambda _: "/usr/bin/uv")
    # relay reachable
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    monkeypatch.setattr("quorus.cli.httpx.get", lambda *a, **kw: mock_resp)

    # Pre-create ~/.claude.json so the registration lands there
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text("{}")

    args = _make_init_args()
    _cmd_init(args)

    config_path = tmp_path / ".quorus" / "config.json"
    assert config_path.exists()
    cfg = json.loads(config_path.read_text())
    assert cfg["relay_url"] == "http://localhost:8080"
    assert cfg["instance_name"] == "agent-3"
    assert cfg["relay_secret"] == "s3cr3t"
    assert "api_key" not in cfg

    claude_cfg = json.loads(claude_json.read_text())
    mcp = claude_cfg["mcpServers"]["quorus"]
    assert mcp["command"] == "uv"
    assert "-m" in mcp["args"]
    assert "quorus.mcp_server" in mcp["args"]


def test_cmd_init_falls_back_to_python_when_uv_missing(tmp_path, monkeypatch, capsys):
    """When uv is not on PATH, the MCP server is registered via sys.executable."""
    import sys

    from quorus.cli import _cmd_init

    monkeypatch.setattr("quorus.cli.Path.home", lambda: tmp_path)
    monkeypatch.setattr("quorus.cli.shutil.which", lambda _: None)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    monkeypatch.setattr("quorus.cli.httpx.get", lambda *a, **kw: mock_resp)

    # Pre-create ~/.claude.json so the registration lands there
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text("{}")

    _cmd_init(_make_init_args())

    claude_cfg = json.loads(claude_json.read_text())
    mcp = claude_cfg["mcpServers"]["quorus"]
    assert mcp["command"] == sys.executable
    assert mcp["args"] == ["-m", "quorus.mcp_server"]

    captured = capsys.readouterr()
    assert "uv" in captured.out  # prints the fallback notice


def test_cmd_init_rejects_invalid_url(monkeypatch):
    """Init exits with code 1 when relay URL is not http/https."""
    from quorus.cli import _cmd_init

    with pytest.raises(SystemExit) as exc_info:
        _cmd_init(_make_init_args(relay_url="ftp://not-valid"))
    assert exc_info.value.code == 1


def test_cmd_init_rejects_empty_name(monkeypatch):
    """Init exits with code 1 when name is empty or whitespace."""
    from quorus.cli import _cmd_init

    with pytest.raises(SystemExit) as exc_info:
        _cmd_init(_make_init_args(name="   "))
    assert exc_info.value.code == 1


def test_cmd_init_warns_on_relay_unreachable(tmp_path, monkeypatch, capsys):
    """Init prints a warning (not an error) when the relay cannot be reached."""
    from quorus.cli import _cmd_init

    monkeypatch.setattr("quorus.cli.Path.home", lambda: tmp_path)
    monkeypatch.setattr("quorus.cli.shutil.which", lambda _: "/usr/bin/uv")
    monkeypatch.setattr(
        "quorus.cli.httpx.get", lambda *a, **kw: (_ for _ in ()).throw(Exception("timeout"))
    )

    _cmd_init(_make_init_args())  # must not raise

    captured = capsys.readouterr()
    assert "Warning" in captured.out or "warning" in captured.out.lower()
    # config should still be written
    assert (tmp_path / ".quorus" / "config.json").exists()


def test_cmd_init_warns_on_existing_config(tmp_path, monkeypatch, capsys):
    """Init prints a warning when an existing config would be overwritten."""
    from quorus.cli import _cmd_init

    monkeypatch.setattr("quorus.cli.Path.home", lambda: tmp_path)
    monkeypatch.setattr("quorus.cli.shutil.which", lambda _: "/usr/bin/uv")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    monkeypatch.setattr("quorus.cli.httpx.get", lambda *a, **kw: mock_resp)

    # Create existing config
    config_dir = tmp_path / ".quorus"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text('{"relay_url": "http://old:8080"}')

    _cmd_init(_make_init_args())

    captured = capsys.readouterr()
    assert "overwriting" in captured.out


# ==================== share/quickjoin tests ====================


def test_encode_join_token_with_secret():
    """Token encodes relay URL, room, expiry, and secret."""
    from quorus.cli import _decode_join_token, _encode_join_token

    token = _encode_join_token(
        relay_url="https://relay.example.com",
        room="test-room",
        secret="my-secret",
    )

    assert token.startswith("quorus://")
    payload = _decode_join_token(token)
    assert payload is not None
    assert payload["r"] == "https://relay.example.com"
    assert payload["n"] == "test-room"
    assert payload["s"] == "my-secret"
    assert "e" in payload  # expiry timestamp


def test_encode_join_token_with_api_key():
    """Token uses api_key field when provided."""
    from quorus.cli import _decode_join_token, _encode_join_token

    token = _encode_join_token(
        relay_url="https://relay.example.com",
        room="test-room",
        api_key="mur_abc123",
    )

    payload = _decode_join_token(token)
    assert payload is not None
    assert payload["k"] == "mur_abc123"
    assert "s" not in payload  # secret not included when api_key present


def test_decode_join_token_invalid_prefix():
    """Tokens without quorus:// prefix are rejected."""
    from quorus.cli import _decode_join_token

    result = _decode_join_token("https://not-a-token")
    assert result is None


def test_decode_join_token_expired(monkeypatch):
    """Expired tokens are rejected."""
    import base64
    import json
    import time

    from quorus.cli import _decode_join_token

    # Create token that expired 1 hour ago
    payload = {
        "r": "https://relay.example.com",
        "n": "test-room",
        "s": "secret",
        "e": int(time.time()) - 3600,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    token = f"quorus://{encoded}"

    result = _decode_join_token(token)
    assert result is None


def test_decode_join_token_malformed():
    """Malformed base64 or JSON is rejected."""
    from quorus.cli import _decode_join_token

    result = _decode_join_token("quorus://not-valid-base64!!!")
    assert result is None


def test_cmd_share_no_relay_configured(capsys, monkeypatch):
    """Share fails gracefully when no relay URL configured."""
    from quorus.cli import _cmd_share

    monkeypatch.setattr("quorus.cli.RELAY_URL", "")

    args = MagicMock()
    args.room = "test-room"
    args.ttl = 7

    _cmd_share(args)

    captured = capsys.readouterr()
    assert "Relay URL not configured" in captured.out


def test_cmd_share_no_auth_configured(capsys, monkeypatch):
    """Share fails gracefully when no auth configured."""
    from quorus.cli import _cmd_share

    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://localhost:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "")
    monkeypatch.setattr("quorus.cli.API_KEY", "")

    args = MagicMock()
    args.room = "test-room"
    args.ttl = 7

    _cmd_share(args)

    captured = capsys.readouterr()
    assert "No auth configured" in captured.out


def test_cmd_quickjoin_invalid_token(capsys):
    """Quickjoin rejects invalid tokens."""
    from quorus.cli import _cmd_quickjoin

    args = MagicMock()
    args.token = "not-a-valid-token"
    args.name = "test-agent"

    _cmd_quickjoin(args)

    captured = capsys.readouterr()
    assert "Invalid or expired token" in captured.out


def test_cmd_quickjoin_missing_fields(capsys):
    """Quickjoin rejects tokens with missing required fields."""
    import base64
    import json
    import time

    from quorus.cli import _cmd_quickjoin

    # Token without room field
    payload = {
        "r": "https://relay.example.com",
        "e": int(time.time()) + 3600,
        "s": "secret",
        # missing "n" (room)
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    token = f"quorus://{encoded}"

    args = MagicMock()
    args.token = token
    args.name = "test-agent"

    _cmd_quickjoin(args)

    captured = capsys.readouterr()
    assert "Token missing required fields" in captured.out


# ---------------------------------------------------------------------------
# Regression tests for quorus join config corruption bug
# ---------------------------------------------------------------------------


def test_cmd_join_preserves_config_when_no_flags(tmp_path, monkeypatch, capsys):
    """Join without flags should use existing config, not corrupt it."""
    from quorus.cli import _cmd_join

    # Set up existing config
    config_dir = tmp_path / ".quorus"
    config_dir.mkdir()
    config_path = config_dir / "config.json"
    original_config = {
        "relay_url": "http://existing-relay:8080",
        "instance_name": "existing-user",
        "relay_secret": "existing-secret",
        "poll_mode": "sse",
    }
    config_path.write_text(json.dumps(original_config))

    # Monkeypatch to use tmp config
    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://existing-relay:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "existing-secret")
    monkeypatch.setattr("quorus.cli.API_KEY", "")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    # Mock the HTTP client
    mock_client = _mock_client(200, [{"id": "r1", "name": "dev-room"}])
    monkeypatch.setattr("quorus.cli._get_client", lambda: mock_client)

    args = MagicMock()
    args.name = "test-agent"
    args.token = None
    args.relay_url = None  # No explicit flag
    args.secret = None  # No explicit flag
    args.api_key = None  # No explicit flag
    args.room = "dev-room"

    _cmd_join(args)

    # Config should NOT have been overwritten
    saved_config = json.loads(config_path.read_text())
    assert saved_config["relay_url"] == "http://existing-relay:8080"
    assert saved_config["relay_secret"] == "existing-secret"


def test_cmd_join_requires_room(capsys, monkeypatch):
    """Join without room flag should show error, not crash."""
    from quorus.cli import _cmd_join

    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://existing-relay:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "existing-secret")

    args = MagicMock()
    args.name = "test-agent"
    args.token = None
    args.relay_url = None
    args.secret = None
    args.api_key = None
    args.room = None  # Missing room

    _cmd_join(args)

    captured = capsys.readouterr()
    assert "Room is required" in captured.out


def test_cmd_join_with_explicit_flags_rewrites_config(tmp_path, monkeypatch, capsys):
    """Join with explicit --relay flag should rewrite config."""
    from quorus.cli import _cmd_join

    # Set up existing config
    config_dir = tmp_path / ".quorus"
    config_dir.mkdir()
    config_path = config_dir / "config.json"
    original_config = {
        "relay_url": "http://old-relay:8080",
        "instance_name": "old-user",
        "relay_secret": "old-secret",
    }
    config_path.write_text(json.dumps(original_config))

    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://old-relay:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "old-secret")
    monkeypatch.setattr("quorus.cli.API_KEY", "")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    # Mock the HTTP client
    mock_client = _mock_client(200, [{"id": "r1", "name": "dev-room"}])
    monkeypatch.setattr("quorus.cli._get_client", lambda: mock_client)

    args = MagicMock()
    args.name = "new-agent"
    args.token = None
    args.relay_url = "http://new-relay:8080"  # Explicit flag
    args.secret = "new-secret"  # Explicit flag
    args.api_key = None
    args.room = "dev-room"

    _cmd_join(args)

    # Config SHOULD have been overwritten with new values
    saved_config = json.loads(config_path.read_text())
    assert saved_config["relay_url"] == "http://new-relay:8080"
    assert saved_config["relay_secret"] == "new-secret"
