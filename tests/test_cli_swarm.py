"""Tests for quorus brief, setup-swarm, board, and resolve CLI commands."""
import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def configure_cli(monkeypatch):
    monkeypatch.setattr("quorus.cli.RELAY_URL", "http://test-relay:8080")
    monkeypatch.setattr("quorus.cli.RELAY_SECRET", "test-secret")
    monkeypatch.setattr("quorus.cli.API_KEY", "")
    monkeypatch.setattr("quorus.cli._cached_jwt", None)
    monkeypatch.setattr("quorus.cli.INSTANCE_NAME", "test-user")


def _mock_response(status_code, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def _mock_async_client(responses: dict | None = None):
    """Build an async httpx client mock.

    responses: {url_fragment: response} mapping. Falls back to a 200 {} default.
    """
    default_resp = _mock_response(200, {})

    def _get_resp(url, *args, **kwargs):
        if responses:
            for fragment, resp in responses.items():
                if fragment in url:
                    return resp
        return default_resp

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get_resp)
    client.post = AsyncMock(side_effect=_get_resp)
    client.aclose = AsyncMock()
    return client


def _args(**kwargs):
    """Build a fake argparse.Namespace."""
    return argparse.Namespace(**kwargs)


# ---------------------------------------------------------------------------
# quorus brief
# ---------------------------------------------------------------------------

def test_brief_posts_message_to_room(monkeypatch, capsys):
    from quorus.cli import _cmd_brief

    post_calls = []

    def mock_post(url, json=None, headers=None, timeout=None):
        post_calls.append((url, json))
        return _mock_response(200, {"id": "m1"})

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("httpx.post", side_effect=mock_post):
        _cmd_brief(_args(room="dev", task="Build the auth module"))

    assert len(post_calls) == 1
    url, payload = post_calls[0]
    assert "/rooms/dev/messages" in url
    assert payload["message_type"] == "brief"
    assert payload["content"] == "Build the auth module"
    assert "brief_id" in payload


def test_brief_skips_decomposition_without_api_key(monkeypatch, capsys):
    from quorus.cli import _cmd_brief

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    post_calls = []

    def mock_post(url, json=None, headers=None, timeout=None):
        post_calls.append((url, json))
        return _mock_response(200, {"id": "m1"})

    with patch("httpx.post", side_effect=mock_post):
        _cmd_brief(_args(room="dev", task="Build the auth module"))

    # Only the brief is posted — no subtask messages
    subtask_posts = [c for _, c in post_calls if c.get("message_type") == "subtask"]
    assert len(subtask_posts) == 0

    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY not set" in captured.out


def test_brief_posts_subtasks_when_api_key_set(monkeypatch, capsys):
    from quorus.cli import _cmd_brief

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    # Mock the anthropic client to return a numbered subtask list
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="1. Implement login\n2. Add JWT signing\n3. Write tests")]

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages.create.return_value = mock_msg

    mock_anthropic_module = MagicMock()
    mock_anthropic_module.Anthropic.return_value = mock_anthropic_client

    post_calls = []

    def mock_post(url, json=None, headers=None, timeout=None):
        resp = _mock_response(200, {"id": "m1"})
        resp.raise_for_status = MagicMock()
        post_calls.append((url, json))
        return resp

    with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}), \
         patch("httpx.post", side_effect=mock_post):
        _cmd_brief(_args(room="dev", task="Build the auth module"))

    subtask_posts = [c for _, c in post_calls if c.get("message_type") == "subtask"]
    assert len(subtask_posts) == 3

    # All subtasks must carry the same brief_id as the parent
    brief_id = post_calls[0][1]["brief_id"]
    for _, payload in post_calls[1:]:
        assert payload["brief_id"] == brief_id

    subtask_contents = [c["content"] for _, c in post_calls if c.get("message_type") == "subtask"]
    assert "Implement login" in subtask_contents
    assert "Add JWT signing" in subtask_contents
    assert "Write tests" in subtask_contents


# ---------------------------------------------------------------------------
# quorus setup-swarm
# ---------------------------------------------------------------------------

def test_setup_swarm_creates_rooms_and_spawns_agents(monkeypatch, capsys):
    from quorus.cli import _cmd_setup_swarm

    post_calls = []

    def mock_post(url, json=None, headers=None):
        post_calls.append(url)
        return _mock_response(200, {"id": "r1"})

    spawn_calls = []

    def mock_spawn(room, name, relay_url, secret):
        spawn_calls.append((room, name))

    with patch("httpx.post", side_effect=mock_post), \
         patch("quorus.cli._spawn_agent", side_effect=mock_spawn):
        _cmd_setup_swarm(_args(
            rooms="backend:Build APIs,frontend:Build UI",
            agents=2,
        ))

    # Rooms created
    room_create_calls = [u for u in post_calls if u.endswith("/rooms")]
    assert len(room_create_calls) == 2

    # Two agents spawned per room = 4 total
    assert len(spawn_calls) == 4
    agent_rooms = [r for r, _ in spawn_calls]
    assert agent_rooms.count("backend") == 2
    assert agent_rooms.count("frontend") == 2

    captured = capsys.readouterr()
    assert "backend" in captured.out
    assert "frontend" in captured.out


def test_setup_swarm_skips_existing_room(monkeypatch, capsys):
    """A 409 response prints 'already exists' and still spawns agents.
    Only error responses (non-200, non-409) skip agent spawning via `continue`.
    """
    from quorus.cli import _cmd_setup_swarm

    def mock_post(url, json=None, headers=None):
        if url.endswith("/rooms") and json and json.get("name") == "failroom":
            return _mock_response(500, {}, "Internal Server Error")
        if url.endswith("/rooms") and json and json.get("name") == "existing":
            return _mock_response(409, {}, "Room already exists")
        return _mock_response(200, {"id": "r1"})

    spawn_calls = []

    def mock_spawn(room, name, relay_url, secret):
        spawn_calls.append((room, name))

    with patch("httpx.post", side_effect=mock_post), \
         patch("quorus.cli._spawn_agent", side_effect=mock_spawn):
        _cmd_setup_swarm(_args(
            rooms="existing:Old room,failroom:Error room,newroom:New room",
            agents=1,
        ))

    agent_rooms = [r for r, _ in spawn_calls]
    # 409 room still gets agents spawned (only error status skips)
    assert "existing" in agent_rooms
    # Error (500) room is skipped
    assert "failroom" not in agent_rooms
    # Normal room gets its agents
    assert "newroom" in agent_rooms

    captured = capsys.readouterr()
    assert "already exists" in captured.out


# ---------------------------------------------------------------------------
# quorus board
# ---------------------------------------------------------------------------

async def test_board_renders_table(monkeypatch, capsys):
    from quorus.cli import _show_board

    rooms = [
        {"id": "r1", "name": "backend", "members": ["alice", "bob"]},
        {"id": "r2", "name": "frontend", "members": ["carol"]},
    ]
    state_r1 = {"active_goal": "Build APIs", "active_agents": ["alice"], "claimed_tasks": []}
    state_r2 = {"active_goal": "Build UI",  "active_agents": ["carol"], "claimed_tasks": []}

    def get_side_effect(url, *args, **kwargs):
        if url.endswith("/rooms"):
            return _mock_response(200, rooms)
        if "r1/state" in url:
            return _mock_response(200, state_r1)
        if "r2/state" in url:
            return _mock_response(200, state_r2)
        return _mock_response(200, {})

    client = AsyncMock()
    client.get = AsyncMock(side_effect=get_side_effect)
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _show_board()

    captured = capsys.readouterr()
    assert "backend" in captured.out
    assert "frontend" in captured.out


async def test_board_room_filter(monkeypatch, capsys):
    from quorus.cli import _show_board

    rooms = [
        {"id": "r1", "name": "backend", "members": ["alice"]},
        {"id": "r2", "name": "frontend", "members": ["carol"]},
    ]
    state = {"active_goal": "Build APIs", "active_agents": ["alice"], "claimed_tasks": []}

    get_urls = []

    def get_side_effect(url, *args, **kwargs):
        get_urls.append(url)
        if url.endswith("/rooms"):
            return _mock_response(200, rooms)
        return _mock_response(200, state)

    client = AsyncMock()
    client.get = AsyncMock(side_effect=get_side_effect)
    client.aclose = AsyncMock()

    with patch("quorus.cli._get_client", return_value=client):
        await _show_board(room_filter="backend")

    # State only fetched for the filtered room
    state_urls = [u for u in get_urls if "/state" in u]
    assert len(state_urls) == 1
    assert "r1" in state_urls[0]

    captured = capsys.readouterr()
    assert "backend" in captured.out
    # frontend room should not appear in the table
    assert "frontend" not in captured.out


# ---------------------------------------------------------------------------
# quorus resolve
# ---------------------------------------------------------------------------

def test_resolve_exits_cleanly_when_no_conflicts(monkeypatch, capsys):
    from quorus.cli import _cmd_resolve

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    mock_anthropic_module = MagicMock()
    git_result = MagicMock()
    git_result.returncode = 0
    git_result.stdout = ""  # no conflicted files
    git_result.stderr = ""

    with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}), \
         patch("subprocess.run", return_value=git_result):
        _cmd_resolve(_args(room=None, model="claude-sonnet-4-6"))

    captured = capsys.readouterr()
    assert "No merge conflicts" in captured.out


def test_resolve_skips_gracefully_without_api_key(monkeypatch, capsys):
    from quorus.cli import _cmd_resolve

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_anthropic_module = MagicMock()

    with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
        with pytest.raises(SystemExit):
            _cmd_resolve(_args(room=None, model="claude-sonnet-4-6"))

    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY not set" in captured.out


def test_resolve_reads_conflict_and_calls_claude(monkeypatch, tmp_path, capsys):
    from quorus.cli import _cmd_resolve

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    # Create a fake conflicted file
    conflict_content = (
        "line before\n"
        "<<<<<<< HEAD\n"
        "def foo():\n"
        "    return 1\n"
        "=======\n"
        "def foo():\n"
        "    return 2\n"
        ">>>>>>> feature-branch\n"
        "line after\n"
    )
    conflict_file = tmp_path / "main.py"
    conflict_file.write_text(conflict_content)

    # git diff returns the conflicted filename
    git_result = MagicMock()
    git_result.returncode = 0
    git_result.stdout = str(conflict_file) + "\n"
    git_result.stderr = ""

    # Anthropic mock
    resolved_text = (
        "Side A returns 1, side B returns 2. Taking side A.\n\n"
        "```python\n"
        "line before\n"
        "def foo():\n"
        "    return 1\n"
        "line after\n"
        "```"
    )
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=resolved_text)]

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages.create.return_value = mock_msg

    mock_anthropic_module = MagicMock()
    mock_anthropic_module.Anthropic.return_value = mock_anthropic_client

    # Decline to apply the resolution (Confirm.ask → False)
    with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}), \
         patch("subprocess.run", return_value=git_result), \
         patch("quorus.cli.Confirm.ask", return_value=False):
        _cmd_resolve(_args(room=None, model="claude-sonnet-4-6"))

    # Claude was called with the conflict content in the prompt
    create_call = mock_anthropic_client.messages.create.call_args
    assert create_call is not None
    prompt = create_call.kwargs["messages"][0]["content"]
    assert "<<<<<<< HEAD" in prompt
    assert str(conflict_file) in prompt
