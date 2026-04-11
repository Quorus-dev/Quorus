from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def configure_cli(monkeypatch):
    monkeypatch.setattr("murmur.cli.RELAY_URL", "http://test-relay:8080")
    monkeypatch.setattr("murmur.cli.RELAY_SECRET", "test-secret")
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
    import httpx
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
    import httpx
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
    client = _mock_client(200, {})

    with patch("murmur.cli.Prompt.ask", side_effect=mock_ask), \
         patch("murmur.cli.Confirm.ask", side_effect=mock_confirm), \
         patch("murmur.cli.asyncio.run") as mock_run, \
         patch("murmur.cli.subprocess.run"), \
         patch("murmur.cli.sys.platform", "darwin"):
        # First asyncio.run call is _list_rooms, second is _auto_join
        mock_run.side_effect = [mock_rooms, None]
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
        idx = confirm_count["n"]
        confirm_count["n"] += 1
        # First confirm (Launch agent?) — say no
        return False

    with patch("murmur.cli.Prompt.ask", side_effect=mock_ask), \
         patch("murmur.cli.Confirm.ask", side_effect=mock_confirm), \
         patch("murmur.cli.asyncio.run", return_value=[]):
        _cmd_add_agent(MagicMock())

    captured = capsys.readouterr()
    assert "Cancelled" in captured.out
