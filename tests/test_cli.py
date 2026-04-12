import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _close_coro(coro):
    """Close a coroutine without awaiting it, preventing RuntimeWarning."""
    if asyncio.iscoroutine(coro):
        coro.close()
    return None


@pytest.fixture(autouse=True)
def configure_cli(monkeypatch):
    monkeypatch.setattr("murmur.cli.RELAY_URL", "http://test-relay:8080")
    monkeypatch.setattr("murmur.cli.RELAY_SECRET", "test-secret")
    monkeypatch.setattr("murmur.cli.API_KEY", "")
    monkeypatch.setattr("murmur.cli._cached_jwt", None)
    monkeypatch.setattr("murmur.cli.INSTANCE_NAME", "test-user")


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
    from murmur.cli import _list_rooms

    mock_rooms = [
        {
            "id": "r1",
            "name": "yc-hack",
            "members": ["alice", "bob"],
            "created_at": "2026-04-11T00:00:00Z",
        }
    ]
    with patch("murmur.cli._get_client", return_value=_mock_client(200, mock_rooms)):
        result = await _list_rooms()
    assert len(result) == 1
    assert result[0]["name"] == "yc-hack"


async def test_cli_create_room():
    from murmur.cli import _create_room

    mock_data = {
        "id": "r1",
        "name": "new-room",
        "members": ["test-user"],
        "created_at": "2026-04-11T00:00:00Z",
    }
    with patch("murmur.cli._get_client", return_value=_mock_client(200, mock_data)):
        result = await _create_room("new-room")
    assert result["name"] == "new-room"


async def test_cli_say():
    from murmur.cli import _say

    rooms_data = [{"id": "r1", "name": "yc-hack", "members": ["test-user"]}]
    msg_data = {"id": "m1", "timestamp": "2026-04-11T00:00:00Z"}

    mock_resp_rooms = _mock_response(200, rooms_data)
    mock_resp_msg = _mock_response(200, msg_data)

    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_resp_rooms)
    client.post = AsyncMock(return_value=mock_resp_msg)
    client.aclose = AsyncMock()

    with patch("murmur.cli._get_client", return_value=client):
        result = await _say("yc-hack", "hello team")
    assert result["id"] == "m1"


async def test_cli_dm():
    from murmur.cli import _dm

    mock_data = {"id": "m1", "timestamp": "2026-04-11T00:00:00Z"}
    with patch("murmur.cli._get_client", return_value=_mock_client(200, mock_data)):
        result = await _dm("bob", "private message")
    assert result["id"] == "m1"


def test_cli_version(capsys):
    from murmur.cli import _cmd_version

    _cmd_version(MagicMock())
    captured = capsys.readouterr()
    assert "murmur-ai" in captured.out
    assert "0.3.0" in captured.out


def test_cli_logs(capsys):
    from murmur.cli import _cmd_logs

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

    from murmur.cli import _cmd_logs

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        _cmd_logs(MagicMock())

    captured = capsys.readouterr()
    assert "Cannot connect" in captured.out
    assert "murmur relay" in captured.out


def test_cli_doctor_all_pass(capsys):
    from murmur.cli import _cmd_doctor

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
         patch("murmur.cli.INSTANCE_NAME", "my-agent"), \
         patch("murmur.cli.RELAY_SECRET", "secret"), \
         patch("murmur.config.resolve_config_file") as mock_resolve:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_resolve.return_value = mock_path
        _cmd_doctor(args)

    captured = capsys.readouterr()
    assert "checks passed" in captured.out


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
    from murmur.cli import _export

    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("murmur.cli._get_client", return_value=client):
        await _export("dev", fmt="json")

    captured = capsys.readouterr()
    import json
    data = json.loads(captured.out)
    assert len(data) == 2
    assert data[0]["from_name"] == "alice"


async def test_export_md_stdout(capsys):
    from murmur.cli import _export

    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("murmur.cli._get_client", return_value=client):
        await _export("dev", fmt="md")

    captured = capsys.readouterr()
    assert "# Room: dev" in captured.out
    assert "**alice**" in captured.out
    assert "**[claim]**" in captured.out


async def test_export_json_to_file(tmp_path):
    from murmur.cli import _export

    out_file = str(tmp_path / "export.json")
    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("murmur.cli._get_client", return_value=client):
        await _export("dev", fmt="json", output=out_file)

    import json
    data = json.loads((tmp_path / "export.json").read_text())
    assert len(data) == 2


async def test_export_md_to_file(tmp_path):
    from murmur.cli import _export

    out_file = str(tmp_path / "export.md")
    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("murmur.cli._get_client", return_value=client):
        await _export("dev", fmt="md", output=out_file)

    content = (tmp_path / "export.md").read_text()
    assert "# Room: dev" in content
    assert "**bob**" in content


async def test_export_empty_room(capsys):
    from murmur.cli import _export

    client = _mock_client(200, [])
    with patch("murmur.cli._get_client", return_value=client):
        await _export("empty-room", fmt="json")

    captured = capsys.readouterr()
    assert "No messages" in captured.out


async def test_export_room_not_found(capsys):
    import httpx

    from murmur.cli import _export

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    with patch("murmur.cli._get_client", return_value=client):
        await _export("ghost-room", fmt="json")

    captured = capsys.readouterr()
    assert "not found" in captured.out


async def test_export_unknown_format(capsys):
    from murmur.cli import _export

    client = _mock_client(200, _SAMPLE_MSGS)
    with patch("murmur.cli._get_client", return_value=client):
        await _export("dev", fmt="csv")

    captured = capsys.readouterr()
    assert "Unknown format" in captured.out


# ── add-agent wizard tests ──────────────────────────────────────────────

def test_add_agent_creates_workspace(tmp_path, monkeypatch):
    from murmur.cli import _cmd_add_agent

    monkeypatch.setattr("murmur.cli.RELAY_URL", "http://test:8080")
    monkeypatch.setattr("murmur.cli.RELAY_SECRET", "test-secret")

    # Redirect workspace to tmp_path
    monkeypatch.setattr("murmur.cli.Path.home", lambda: tmp_path)

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

    with patch("murmur.cli.Prompt.ask", side_effect=mock_ask), \
         patch("murmur.cli.Confirm.ask", side_effect=mock_confirm), \
         patch("murmur.cli.asyncio.run", side_effect=_close_coro_with_return), \
         patch("murmur.cli.subprocess.run"), \
         patch("murmur.cli.sys.platform", "darwin"):
        _cmd_add_agent(MagicMock())

    workspace = tmp_path / "murmur-agents" / "test-bot"
    assert workspace.exists()
    assert (workspace / ".mcp.json").exists()
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "settings.json").exists()

    import json
    claude_md = (workspace / "CLAUDE.md").read_text()
    assert "test-bot" in claude_md
    assert "dev" in claude_md

    mcp = json.loads((workspace / ".mcp.json").read_text())
    assert mcp["mcpServers"]["murmur"]["env"]["INSTANCE_NAME"] == "test-bot"


# ── connect command tests ────────────────────────────────────────────────

def test_connect_codex(capsys):
    from murmur.cli import _cmd_connect

    args = MagicMock()
    args.platform = "codex"
    args.room = "dev"
    args.name = "codex-bot"

    client = _mock_client(200, {})
    with patch("murmur.cli._get_client", return_value=client), \
         patch("murmur.cli.asyncio.run", side_effect=_close_coro):
        _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Codex Agent Setup" in captured.out
    assert "codex-bot" in captured.out
    assert "/messages/codex-bot" in captured.out


def test_connect_cursor(capsys):
    from murmur.cli import _cmd_connect

    args = MagicMock()
    args.platform = "cursor"
    args.room = "dev"
    args.name = "cursor-bot"

    client = _mock_client(200, {})
    with patch("murmur.cli._get_client", return_value=client), \
         patch("murmur.cli.asyncio.run", side_effect=_close_coro):
        _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Cursor Agent Setup" in captured.out
    assert "mcpServers" in captured.out
    assert "cursor-bot" in captured.out


def test_connect_ollama(capsys):
    from murmur.cli import _cmd_connect

    args = MagicMock()
    args.platform = "ollama"
    args.room = "dev"
    args.name = "llama-bot"

    client = _mock_client(200, {})
    with patch("murmur.cli._get_client", return_value=client), \
         patch("murmur.cli.asyncio.run", side_effect=_close_coro):
        _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Ollama Agent Setup" in captured.out
    assert "llama-bot" in captured.out
    assert "ollama_agent.py" in captured.out


def test_connect_claude(capsys):
    from murmur.cli import _cmd_connect

    args = MagicMock()
    args.platform = "claude"
    args.room = "dev"
    args.name = "claude-bot"

    client = _mock_client(200, {})
    with patch("murmur.cli._get_client", return_value=client), \
         patch("murmur.cli.asyncio.run", side_effect=_close_coro):
        _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Claude Code Agent Setup" in captured.out
    assert "murmur add-agent" in captured.out


# ── search command tests ─────────────────────────────────────────────────

async def test_search_by_keyword(capsys):
    from murmur.cli import _search

    results = [_SAMPLE_MSGS[0]]  # only "hello world"
    client = _mock_client(200, results)
    with patch("murmur.cli._get_client", return_value=client):
        await _search("dev", query="hello")

    captured = capsys.readouterr()
    assert "alice" in captured.out
    assert "1 matches" in captured.out


async def test_search_by_sender(capsys):
    from murmur.cli import _search

    results = [_SAMPLE_MSGS[1]]  # only bob
    client = _mock_client(200, results)
    with patch("murmur.cli._get_client", return_value=client):
        await _search("dev", sender="bob")

    captured = capsys.readouterr()
    assert "bob" in captured.out


async def test_search_by_message_type(capsys):
    from murmur.cli import _search

    results = [_SAMPLE_MSGS[1]]  # claim type
    client = _mock_client(200, results)
    with patch("murmur.cli._get_client", return_value=client):
        await _search("dev", message_type="claim")

    captured = capsys.readouterr()
    assert "claim" in captured.out


async def test_search_no_results(capsys):
    from murmur.cli import _search

    client = _mock_client(200, [])
    with patch("murmur.cli._get_client", return_value=client):
        await _search("dev", query="nonexistent")

    captured = capsys.readouterr()
    assert "No matching" in captured.out


async def test_search_room_not_found(capsys):
    import httpx

    from murmur.cli import _search

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    with patch("murmur.cli._get_client", return_value=client):
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
    from murmur.cli import _metrics

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

    with patch("murmur.cli._get_client", return_value=client):
        await _metrics("dev")

    captured = capsys.readouterr()
    assert "Agent Activity" in captured.out
    assert "alice" in captured.out
    assert "bob" in captured.out
    assert "Message Types" in captured.out
    assert "Relay Summary" in captured.out
    assert "100" in captured.out  # total sent


async def test_metrics_empty_room(capsys):
    from murmur.cli import _metrics

    hist_resp = MagicMock()
    hist_resp.status_code = 200
    hist_resp.json.return_value = []
    hist_resp.raise_for_status = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(return_value=hist_resp)
    client.aclose = AsyncMock()

    with patch("murmur.cli._get_client", return_value=client):
        await _metrics("empty")

    captured = capsys.readouterr()
    assert "No messages" in captured.out


async def test_metrics_room_not_found(capsys):
    import httpx

    from murmur.cli import _metrics

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()

    with patch("murmur.cli._get_client", return_value=client):
        await _metrics("ghost")

    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_connect_unknown_platform(capsys):
    from murmur.cli import _cmd_connect

    args = MagicMock()
    args.platform = "gpt"
    args.room = "dev"
    args.name = "gpt-bot"

    _cmd_connect(args)

    captured = capsys.readouterr()
    assert "Unknown platform" in captured.out


def test_add_agent_cancelled(monkeypatch, capsys):
    from murmur.cli import _cmd_add_agent

    monkeypatch.setattr("murmur.cli.RELAY_URL", "http://test:8080")
    monkeypatch.setattr("murmur.cli.RELAY_SECRET", "test-secret")

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

    with patch("murmur.cli.Prompt.ask", side_effect=mock_ask), \
         patch("murmur.cli.Confirm.ask", side_effect=mock_confirm), \
         patch("murmur.cli.asyncio.run", side_effect=_close_coro_return_empty):
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
        "murmur/relay.py": {
            "held_by": "arav-agent-1",
            "claimed_by": "arav-agent-1",
            "expires_at": "2026-04-11T10:05:00Z",
            "lock_token": "abc12345-token",
        },
        "murmur/mcp.py": {
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
    from murmur.cli import _room_state

    client = _mock_client(200, _SAMPLE_STATE)
    with patch("murmur.cli._get_client", return_value=client):
        await _room_state("murmur-dev")

    captured = capsys.readouterr()
    assert "Build distributed mutex locking layer" in captured.out
    assert "arav-agent-1" in captured.out
    assert "3 online" in captured.out
    assert "murmur/relay.py" in captured.out
    assert "47" in captured.out


async def test_cmd_room_state_no_goal(capsys):
    from murmur.cli import _room_state

    state = dict(_SAMPLE_STATE)
    state["active_goal"] = None
    state["locked_files"] = {}
    client = _mock_client(200, state)
    with patch("murmur.cli._get_client", return_value=client):
        await _room_state("murmur-dev")

    captured = capsys.readouterr()
    assert "No active goal" in captured.out
    assert "None" in captured.out


async def test_cmd_room_state_not_found(capsys):
    import httpx

    from murmur.cli import _room_state

    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=resp
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    with patch("murmur.cli._get_client", return_value=client):
        await _room_state("ghost-room")

    captured = capsys.readouterr()
    assert "not found" in captured.out


# ── locks command tests ──────────────────────────────────────────────────────


async def test_cmd_room_locks_shows_table(capsys):
    from murmur.cli import _room_locks

    client = _mock_client(200, _SAMPLE_STATE)
    with patch("murmur.cli._get_client", return_value=client):
        await _room_locks("murmur-dev")

    captured = capsys.readouterr()
    assert "murmur/relay.py" in captured.out
    assert "arav-agent-1" in captured.out
    assert "abc12345" in captured.out


async def test_cmd_room_locks_empty(capsys):
    from murmur.cli import _room_locks

    state = dict(_SAMPLE_STATE)
    state["locked_files"] = {}
    client = _mock_client(200, state)
    with patch("murmur.cli._get_client", return_value=client):
        await _room_locks("murmur-dev")

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
    {"id": "r1", "name": "murmur-dev", "members": ["arav", "arav-agent-1"]},
    {"id": "r2", "name": "murmur-prod", "members": ["arav-agent-2"]},
    {"id": "r3", "name": "staging", "members": []},
]

_PRESENCE_LIST = [
    {"name": "arav", "online": True},
    {"name": "arav-agent-1", "online": True},
    {"name": "arav-agent-2", "online": False},
]


async def test_cmd_usage_shows_stats(capsys):
    from murmur.cli import _usage

    analytics_resp = _mock_response(200, _ANALYTICS_USAGE)
    analytics_resp.is_success = True
    rooms_resp = _mock_response(200, _ROOMS_LIST)
    rooms_resp.is_success = True
    presence_resp = _mock_response(200, _PRESENCE_LIST)
    presence_resp.is_success = True

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[analytics_resp, rooms_resp, presence_resp])
    client.aclose = AsyncMock()

    with patch("murmur.cli._get_client", return_value=client):
        await _usage()

    captured = capsys.readouterr()
    assert "1,247" in captured.out
    assert "arav-agent-1" in captured.out
    assert "342" in captured.out


async def test_cmd_usage_relay_down(capsys):
    import httpx

    from murmur.cli import _usage

    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.aclose = AsyncMock()

    with patch("murmur.cli._get_client", return_value=client):
        await _usage()

    captured = capsys.readouterr()
    assert "Cannot connect" in captured.out


# -----------------------------------------------------------------------------
# inbox / hook command tests
# -----------------------------------------------------------------------------


def test_cmd_inbox_no_messages(capsys):
    """When no messages, inbox should exit silently."""
    import httpx as httpx_mod

    from murmur.cli import _cmd_inbox

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

    from murmur.cli import _cmd_inbox

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
    assert "[murmur]" in captured.out
    assert "2 new messages" in captured.out
    assert "alice" in captured.out
    assert "bob" in captured.out


def test_cmd_inbox_json_output(capsys):
    """--json flag should output raw JSON."""
    import httpx as httpx_mod

    from murmur.cli import _cmd_inbox

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

    from murmur.cli import _cmd_inbox

    with patch.object(httpx_mod, "get", side_effect=httpx_mod.ConnectError("refused")):
        args = MagicMock()
        args.quiet = False
        args.json = False
        _cmd_inbox(args)

    captured = capsys.readouterr()
    assert captured.out == ""  # Silent exit


def test_cmd_hook_status_not_configured(capsys, tmp_path):
    """Hook status should show not configured when no hook exists."""
    from murmur.cli import _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    with patch("murmur.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "status"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "not configured" in captured.out


def test_cmd_hook_enable_creates_hook(capsys, tmp_path):
    """Hook enable should create the settings file with hook config."""
    import json

    from murmur.cli import _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    with patch("murmur.cli.CLAUDE_SETTINGS_PATH", settings_path):
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
    assert any("murmur inbox" in str(h) for h in hooks)


def test_cmd_hook_enable_already_enabled(capsys, tmp_path):
    """Hook enable when already enabled should show warning."""
    import json

    from murmur.cli import MURMUR_HOOK_CONFIG, _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [MURMUR_HOOK_CONFIG]}
    }))

    with patch("murmur.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "enable"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "already enabled" in captured.out


def test_cmd_hook_disable_removes_hook(capsys, tmp_path):
    """Hook disable should remove the murmur hook from settings."""
    import json

    from murmur.cli import MURMUR_HOOK_CONFIG, _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [MURMUR_HOOK_CONFIG]}
    }))

    with patch("murmur.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "disable"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "disabled" in captured.out

    # Verify hook was removed
    settings = json.loads(settings_path.read_text())
    hooks = settings["hooks"]["UserPromptSubmit"]
    assert not any("murmur inbox" in str(h) for h in hooks)


def test_cmd_hook_status_when_enabled(capsys, tmp_path):
    """Hook status should show enabled when hook exists."""
    import json

    from murmur.cli import MURMUR_HOOK_CONFIG, _cmd_hook

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [MURMUR_HOOK_CONFIG]}
    }))

    with patch("murmur.cli.CLAUDE_SETTINGS_PATH", settings_path):
        args = MagicMock()
        args.action = "status"
        _cmd_hook(args)

    captured = capsys.readouterr()
    assert "enabled" in captured.out
