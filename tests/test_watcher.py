"""Tests for Watcher — background context.md writer."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from quorus_cli.watcher import Watcher


@pytest.fixture
async def temp_context_dir():
  """Create a temporary directory for context files."""
  with tempfile.TemporaryDirectory() as tmpdir:
    yield Path(tmpdir)


async def test_watcher_init(temp_context_dir: Path):
  """Watcher can be initialized with parameters."""
  context_path = temp_context_dir / ".quorus" / "context.md"
  watcher = Watcher(
      relay_url="http://localhost:8080",
      auth_headers={"Authorization": "Bearer test"},
      room_name="test-room",
      agent_name="test-agent",
      sse_token="test-token",
      context_path=context_path,
  )
  assert watcher.room_name == "test-room"
  assert watcher.agent_name == "test-agent"
  assert not watcher._running


async def test_watcher_format_context_md():
  """Watcher formats context dict as Markdown correctly."""
  context_path = Path("/tmp/test")
  watcher = Watcher(
      relay_url="http://localhost:8080",
      auth_headers={},
      room_name="dev-room",
      agent_name="alice",
      sse_token="test-token",
      context_path=context_path,
  )

  context = {
      "room": "dev-room",
      "agent": "alice",
      "snapshot_at": "2026-04-11T22:30:00Z",
      "schema_version": "1.0",
      "room_state": {
          "active_agents": ["alice", "bob"],
          "message_count": 42,
          "claimed_tasks": [
              {
                  "file_path": "src/auth.py",
                  "claimed_by": "alice",
                  "claimed_at": "2026-04-11T22:29:00Z",
              }
          ],
          "locked_files": {
              "src/auth.py": {
                  "held_by": "alice",
              }
          },
      },
      "messages": [
          {
              "timestamp": "2026-04-11T22:29:30Z",
              "from_name": "alice",
              "type": "claim",
              "content": "CLAIM: auth module",
          },
          {
              "timestamp": "2026-04-11T22:29:40Z",
              "from_name": "bob",
              "type": "chat",
              "content": "Got it, starting tests",
          },
      ],
  }

  md = watcher._format_context_md(context)

  # Check key sections are present
  assert "# dev-room" in md
  assert "**Agent**: alice" in md
  assert "## Room State" in md
  assert "**Active agents**: alice, bob" in md
  assert "**Message count**: 42" in md
  assert "### Claimed Tasks" in md
  assert "src/auth.py" in md
  assert "### Locked Files" in md
  assert "## Recent Messages" in md
  assert "CLAIM: auth module" in md
  assert "Got it, starting tests" in md


async def test_watcher_write_context(temp_context_dir: Path):
  """Watcher writes context.md atomically."""
  context_path = temp_context_dir / ".quorus" / "context.md"
  watcher = Watcher(
      relay_url="http://localhost:8080",
      auth_headers={},
      room_name="test-room",
      agent_name="test-agent",
      sse_token="test-token",
      context_path=context_path,
  )

  context = {
      "room": "test-room",
      "agent": "test-agent",
      "snapshot_at": "2026-04-11T22:30:00Z",
      "schema_version": "1.0",
      "room_state": None,
      "messages": [],
  }

  await watcher._write_context(context)

  # Check file was created
  assert context_path.exists()

  # Check content
  content = context_path.read_text()
  assert "# test-room" in content
  assert "**Agent**: test-agent" in content


async def test_watcher_creates_quorus_dir(temp_context_dir: Path):
  """Watcher creates .quorus directory if it doesn't exist."""
  context_path = temp_context_dir / ".quorus" / "subdir" / "context.md"
  assert not context_path.parent.exists()

  watcher = Watcher(
      relay_url="http://localhost:8080",
      auth_headers={},
      room_name="test-room",
      agent_name="test-agent",
      sse_token="test-token",
      context_path=context_path,
  )

  context = {
      "room": "test-room",
      "agent": "test-agent",
      "snapshot_at": "2026-04-11T22:30:00Z",
      "schema_version": "1.0",
      "room_state": None,
      "messages": [],
  }

  await watcher._write_context(context)

  assert context_path.exists()
  assert context_path.parent.exists()


async def test_watcher_format_no_room_state():
  """Watcher formats context MD when room_state is None."""
  context_path = Path("/tmp/test")
  watcher = Watcher(
      relay_url="http://localhost:8080",
      auth_headers={},
      room_name="test-room",
      agent_name="test-agent",
      sse_token="test-token",
      context_path=context_path,
  )

  context = {
      "room": "test-room",
      "agent": "test-agent",
      "snapshot_at": "2026-04-11T22:30:00Z",
      "schema_version": "1.0",
      "room_state": None,
      "messages": [],
  }

  md = watcher._format_context_md(context)

  # Should still have header, but no room state section
  assert "# test-room" in md
  assert "## Room State" not in md
